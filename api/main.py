"""
Read-only analytics API over the stablecoin_transfers ClickHouse table.

Design notes:
- Fixed, named endpoints only. Each runs one pre-written, parameterized query.
  There is NO general query endpoint, so the public API cannot be used to run
  arbitrary SQL against the database.

Methodology - mints & burns:
  A transfer FROM the zero address is a mint; a transfer TO the zero (or dead)
  address is a burn. These are supply changes, not participant-to-participant
  payments, so every PAYMENT/VOLUME metric excludes both sides. The raw table
  keeps them intact - the exclusion lives only in these queries.
  Exception: /api/top-receivers excludes only the burn (to-side) so that
  legitimate recipients of freshly-minted tokens still appear.
"""

import os
import clickhouse_connect
from fastapi import FastAPI, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware

# --- config from environment ---
CH_HOST = os.environ.get("CLICKHOUSE_HOST", "clickhouse")   # service name in compose
CH_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))    # HTTP port
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CH_DATABASE = os.environ.get("CLICKHOUSE_DATABASE", "default")

# Zero/burn addresses excluded from payment metrics.
ZERO_ADDR = "0x0000000000000000000000000000000000000000"
DEAD_ADDR = "0x000000000000000000000000000000000000dead"

app = FastAPI(title="Celo Stablecoins Flow Analytics API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# Guard rails, added after the 2026-09-14 outage. Two separate failures were
# invisible that day and both are covered here:
#
#   1. No socket timeout. When ClickHouse stopped answering, every request hung
#      indefinitely. These endpoints are sync `def`, so each hung request held a
#      threadpool worker; once all were held, every DB route stalled while the
#      async ones (/docs, /openapi.json) kept answering - which is exactly the
#      symptom that made the server look half-alive.
#   2. No per-query ceiling. One heavy query could consume the server's whole
#      memory budget and take the instance down with it. A query that asks for
#      too much should die alone.
CH_CONNECT_TIMEOUT = int(os.environ.get("CLICKHOUSE_CONNECT_TIMEOUT", "3"))
# Server-side query cap, and the socket timeout that must outlive it: if the
# socket gave up first the server would keep burning CPU on a query nobody is
# waiting for any more.
CH_MAX_EXECUTION = int(os.environ.get("CLICKHOUSE_MAX_EXECUTION", "25"))
CH_QUERY_TIMEOUT = CH_MAX_EXECUTION + 5

# Result-cache lifetime. Kept just under the board's 30s refresh so a viewer
# sees fresh numbers each tick rather than the same ones twice; the staleness
# this introduces is therefore never worse than the refresh interval already is.
CH_CACHE_TTL = int(os.environ.get("CLICKHOUSE_CACHE_TTL", "25"))

# Per-query limits sent with every statement, sized against the server's 3.26
# GiB ceiling so one runaway query fails while the server stays up.
CH_SETTINGS = {
    "max_memory_usage": 1_500_000_000,
    "max_execution_time": CH_MAX_EXECUTION,
    # THROW, not "break". "break" returns whatever rows the query had managed
    # to produce - for an aggregation that is usually none - with HTTP 200, so
    # a query that ran out of time is indistinguishable from a window with no
    # activity. The board then draws empty charts and advances its "Updated"
    # stamp, reporting healthy while showing nothing. A slow query must fail
    # loudly; partial aggregates are not a safe default.
    "timeout_overflow_mode": "throw",

    # Result cache. Every viewer re-runs the identical 24h aggregate every 30s
    # and the answer barely moves, so the work is almost entirely redundant:
    # on the VM those queries scan ~7.8M rows at ~400K rows/s and take 18-26s,
    # against 0.17s for the same query on a laptop. Caching the RESULT is the
    # cheap half of the fix; pre-aggregated rollups are the durable half.
    "use_query_cache": 1,
    "query_cache_ttl": CH_CACHE_TTL,
    # Mandatory here, not optional: every query filters on now(), and the
    # default for a nondeterministic function is to refuse with "Code: 704 -
    # the query result was not cached". "save" stores it anyway, which is
    # correct because the entry expires after query_cache_ttl regardless.
    "query_cache_nondeterministic_function_handling": "save",
}


def client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER,
        password=CH_PASSWORD, database=CH_DATABASE,
        connect_timeout=CH_CONNECT_TIMEOUT,
        send_receive_timeout=CH_QUERY_TIMEOUT,
        settings=CH_SETTINGS,
    )

"""Turn a clickhouse_connect query result into a list of plain dicts."""
def rows_to_dicts(result):
    cols = result.column_names
    return [dict(zip(cols, row)) for row in result.result_rows]


"""Common params: the zero/dead addresses plus any endpoint-specific ones."""
def base_params(**extra):
    return {"zero": ZERO_ADDR, "dead": DEAD_ADDR, **extra}


@app.get("/api/health")
def health(response: Response):
    """Liveness AND readiness. Returns 503 when the database is unreachable, so
    a monitor can tell the difference without parsing the body - on 2026-09-14
    this endpoint hung instead of reporting "degraded", because a socket with no
    timeout never raises the exception the except block was waiting for. The
    timeouts on client() are what make this branch reachable at all."""
    try:
        c = client()
        c.query("SELECT 1")
        return {"status": "ok", "database": "reachable"}
    except Exception as e:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable", "error": str(e)}


@app.get("/api/summary")
def summary(hours: int = Query(24, ge=1, le=168)):
    c = client()
    q = """
        SELECT
            round(sum(amount_usd), 2)   AS total_usd_volume,
            count()                     AS transfer_count,
            uniqExact(from_address)     AS unique_senders,
            uniqExact(symbol)           AS active_coins
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalHour(%(hours)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
    """
    res = c.query(q, parameters=base_params(hours=hours))
    data = rows_to_dicts(res)
    return data[0] if data else {}


@app.get("/api/volume-by-symbol")
def volume_by_symbol(hours: int = Query(24, ge=1, le=168),
                     limit: int = Query(12, ge=1, le=50)):
    """USD volume and transfer count per stablecoin over the last N hours,
    ranked, top `limit` coins. Mints/burns excluded."""
    c = client()
    q = """
        SELECT
            symbol,
            round(sum(amount_usd), 2) AS usd_volume,
            count()                   AS transfers
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalHour(%(hours)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY symbol
        ORDER BY usd_volume DESC
        LIMIT %(limit)s
    """
    res = c.query(q, parameters=base_params(hours=hours, limit=limit))
    return rows_to_dicts(res)


@app.get("/api/volume-by-peg")
def volume_by_peg(hours: int = Query(24, ge=1, le=168)):
    """USD volume grouped by peg currency (USD vs EUR vs regional) - shows how
    multi-currency the activity really is. Mints/burns excluded."""
    c = client()
    q = """
        SELECT
            peg,
            round(sum(amount_usd), 2) AS usd_volume,
            count()                   AS transfers
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalHour(%(hours)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY peg
        ORDER BY usd_volume DESC
    """
    res = c.query(q, parameters=base_params(hours=hours))
    return rows_to_dicts(res)


@app.get("/api/volume-per-minute")
def volume_per_minute(minutes: int = Query(60, ge=5, le=1440)):
    """Combined USD volume per minute over the last N minutes - the live trend.
    Mints/burns excluded."""
    c = client()
    q = """
        SELECT
            toStartOfMinute(block_timestamp) AS minute,
            round(sum(amount_usd), 2)        AS usd_volume,
            count()                          AS transfers
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalMinute(%(minutes)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY minute
        ORDER BY minute
    """
    res = c.query(q, parameters=base_params(minutes=minutes))
    rows = rows_to_dicts(res)
    for r in rows:
        r["minute"] = r["minute"].isoformat()
    return rows


@app.get("/api/top-receivers")
def top_receivers(hours: int = Query(24, ge=1, le=168),
                  limit: int = Query(20, ge=1, le=100)):
    """Largest recipient addresses by USD received. Excludes the burn (to-side)
    address only - recipients of freshly-minted tokens are legitimate receivers,
    so the from-side (mint) is NOT excluded here."""
    c = client()
    q = """
        SELECT
            to_address                AS receiver,
            round(sum(amount_usd), 2) AS received_usd,
            count()                   AS transfers
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalHour(%(hours)s)
          AND to_address NOT IN (%(zero)s, %(dead)s)
        GROUP BY to_address
        ORDER BY received_usd DESC
        LIMIT %(limit)s
    """
    res = c.query(q, parameters=base_params(hours=hours, limit=limit))
    return rows_to_dicts(res)


@app.get("/api/payments-per-minute")
def payments_per_minute(minutes: int = Query(60, ge=5, le=1440)):
    """Distinct-transaction payment count per minute (mints/burns excluded).
    Uses uniqExact(tx_hash) so multiple transfer logs in one tx count once."""
    c = client()
    q = """
        SELECT
            toStartOfMinute(block_timestamp) AS minute,
            uniqExact(tx_hash)               AS payments
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalMinute(%(minutes)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY minute
        ORDER BY minute
    """
    res = c.query(q, parameters=base_params(minutes=minutes))
    rows = rows_to_dicts(res)
    for r in rows:
        r["minute"] = r["minute"].isoformat()
    return rows


@app.get("/api/senders-per-minute")
def senders_per_minute(minutes: int = Query(60, ge=5, le=1440)):
    """Unique sending addresses per minute, across ALL stablecoins combined.

    Deliberately NOT broken out per symbol: an address that sends two different
    stablecoins in the same minute is one sender, not two. uniqExact over the
    whole minute dedupes it. Mints/burns excluded."""
    c = client()
    q = """
        SELECT
            toStartOfMinute(block_timestamp) AS minute,
            uniqExact(from_address)          AS senders
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalMinute(%(minutes)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY minute
        ORDER BY minute
    """
    res = c.query(q, parameters=base_params(minutes=minutes))
    rows = rows_to_dicts(res)
    for r in rows:
        r["minute"] = r["minute"].isoformat()
    return rows


@app.get("/api/payments-per-minute-by-symbol")
def payments_per_minute_by_symbol(minutes: int = Query(60, ge=5, le=1440)):
    """Payment count per minute, broken out per stablecoin (long format: one row
    per minute+symbol). Mints/burns excluded. Frontend pivots to series.
    """
    c = client()
    q = """
        SELECT
            toStartOfMinute(block_timestamp) AS minute,
            symbol,
            uniqExact(tx_hash)               AS payments
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalMinute(%(minutes)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY minute, symbol
        ORDER BY minute, symbol
    """
    res = c.query(q, parameters=base_params(minutes=minutes))
    rows = rows_to_dicts(res)
    for r in rows:
        r["minute"] = r["minute"].isoformat()
    return rows


@app.get("/api/volume-by-symbol-per-minute")
def volume_by_symbol_per_minute(minutes: int = Query(60, ge=5, le=1440)):
    """USD volume per minute, broken out per stablecoin (long format:
    one row per minute+symbol). Mints/burns excluded. Frontend pivots to series."""
    c = client()
    q = """
        SELECT
            toStartOfMinute(block_timestamp) AS minute,
            symbol,
            round(sum(amount_usd), 2)        AS usd_volume
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalMinute(%(minutes)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address NOT IN (%(zero)s, %(dead)s)
        GROUP BY minute, symbol
        ORDER BY minute, symbol
    """
    res = c.query(q, parameters=base_params(minutes=minutes))
    rows = rows_to_dicts(res)
    for r in rows:
        r["minute"] = r["minute"].isoformat()
    return rows

@app.get("/api/volume-by-tx-type-per-minute")
def volume_tx_type_per_minute(minutes: int =Query(60, ge=5, le=1440)):
    c = client()
    q = """
        SELECT
            toStartOfMinute(block_timestamp) AS minute,
            tx_type,
            round(sum(amount_usd), 2) AS usd_volume
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalMinute(%(minutes)s)
        AND from_address NOT IN (%(zero)s, %(dead)s)
        AND to_address NOT IN (%(zero)s, %(dead)s)
        GROUP BY minute, tx_type
        ORDER BY minute, tx_type

    """
    res = c.query(q, parameters=base_params(minutes=minutes))
    rows = rows_to_dicts(res)
    for r in rows:
        r["minute"] = r["minute"].isoformat()
    return rows


@app.get("/api/volume-by-tx-type")
def volume_by_type(hours: int = Query(24, ge=1, le=168)):
    """USD volume grouped by tx type(p2p v lending v swaps, etc)."""
    c = client()
    q = """
        SELECT
            tx_type,
            round(sum(amount_usd), 2) AS usd_volume,
            count() AS transfers
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalHour(%(hours)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address NOT IN (%(zero)s, %(dead)s)
        GROUP BY tx_type
        ORDER BY usd_volume DESC
    """
    res = c.query(q, parameters=base_params(hours=hours))
    return rows_to_dicts(res)



@app.get("/api/avg-payment-by-symbol")
def avg_payment_by_symbol(hours: int = Query(1, ge=1, le=168),
                          limit: int = Query(15, ge=1, le=50)):
    """Average payment size (USD) per stablecoin over the last N hours, with
    median alongside so a few whales don't distort it. Mints/burns excluded."""
    c = client()
    q = """
        SELECT
            symbol,
            round(avg(amount_usd), 2)    AS avg_usd,
            round(median(amount_usd), 2) AS median_usd,
            count()                      AS transfers
        FROM stablecoin_transfers
        WHERE block_timestamp > now() - toIntervalHour(%(hours)s)
          AND from_address NOT IN (%(zero)s, %(dead)s)
          AND to_address   NOT IN (%(zero)s, %(dead)s)
        GROUP BY symbol
        ORDER BY avg_usd DESC
        LIMIT %(limit)s
    """
    res = c.query(q, parameters=base_params(hours=hours, limit=limit))
    return rows_to_dicts(res)