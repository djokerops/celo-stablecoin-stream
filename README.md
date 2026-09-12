# celo-stablecoin-stream

A live stream of stablecoin payments on Celo. It watches every `Transfer` event for 29 stablecoins as blocks land, converts each amount to USD, and lands it in ClickHouse for querying seconds after it happened on-chain.

**[See live dashboard](https://celoflow-nine.vercel.app)**

## What it does

```
Celo (forno RPC) --> producer (Python) --> Redpanda --> ClickHouse --> queries
```

1. **Producer** ([producer/stream_stablecoins.py](producer/stream_stablecoins.py)) polls the chain tip every 5s, pulls `Transfer` logs for each tracked token, prices them in USD, and publishes one JSON event per transfer to the `celo_stablecoins` topic.
2. **Redpanda** is the Kafka-compatible buffer, so the chain reader and the database don't have to keep pace with each other.
3. **ClickHouse** consumes the topic through a Kafka engine table, and a materialized view writes each row into a `MergeTree` table partitioned by day.

Tracked tokens (29):

- **Mento** — `USDm`, `EURm`, `BRLm`, `AUDm`, `CADm`, `CHFm`, `COPm`, `GBPm`, `GHSm`, `JPYm`, `KESm`, `NGNm`, `PHPm`, `XOFm`, `ZARm`
- **Ripio** — `wARS`, `wBRL`, `wMXN`, `wCOP`, `wPEN`, `wCLP`
- **Other issuers** — `USDC`, `USDT`, `BRLA`, `VCHF`, `VGBP`, `USDGLO`, `USDM`, `cNGN`

**USD pricing.** USD-pegged tokens are 1.0. Everything else reads `medianRate` from Mento's `SortedOracles`, but only accepts it within 0.5x–2x of a reference peg — Mento feeds vary in scale and direction, so a raw `num/den` isn't always a clean USD price. Out-of-band reads fall back to the hardcoded reference rate. Treat `amount_usd` as indicative, not settlement-grade.

## Layout

| Path | What |
|---|---|
| [docker-compose.yml](docker-compose.yml) | Redpanda, Redpanda Console (`:8085`), ClickHouse (`:8123`) |
| [clickhouse/01_storage.sql](clickhouse/01_storage.sql) | `stablecoin_transfers` — the MergeTree table |
| [clickhouse/02_kafka.sql](clickhouse/02_kafka.sql) | `stablecoin_transfers_kafka` — Kafka engine consumer |
| [clickhouse/03_matview.sql](clickhouse/03_matview.sql) | materialized view wiring the two together |
| [producer/stream_stablecoins.py](producer/stream_stablecoins.py) | the chain reader |

## Running it

**1. Credentials.** Create a `.env` (gitignored) in the repo root:

```env
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=choose_something
```

**2. Start the infra.**

```bash
docker compose up -d
```

**3. Create the schema** — in order, since the view depends on both tables:

```bash
set -a; . ./.env; set +a
for f in clickhouse/0*.sql; do
  docker exec -i clickhouse clickhouse-client \
    --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" \
    --multiquery < "$f"
done
```

**4. Start the producer.**

```bash
cd producer
pipenv install
pipenv run python stream_stablecoins.py
```

It prints a line per block with transfers and runs until `Ctrl+C`. Redpanda Console at [localhost:8085](http://localhost:8085) shows the raw topic if you want to watch events arrive.

## Querying

```bash
set -a; . ./.env; set +a
docker exec -it clickhouse clickhouse-client \
  --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD"
```

Recent payments:

```sql
SELECT block_timestamp, symbol, amount_token, amount_usd, from_address, to_address
FROM stablecoin_transfers
ORDER BY block_timestamp DESC
LIMIT 20;
```

Volume by token over the last hour:

```sql
SELECT symbol, count() AS transfers, round(sum(amount_usd)) AS usd
FROM stablecoin_transfers
WHERE block_timestamp > now() - INTERVAL 1 HOUR
GROUP BY symbol
ORDER BY usd DESC;
```

Per-minute throughput:

```sql
SELECT toStartOfMinute(block_timestamp) AS minute, count() AS transfers, round(sum(amount_usd)) AS usd
FROM stablecoin_transfers
GROUP BY minute
ORDER BY minute DESC
LIMIT 30;
```

## Notes

- `docker compose stop` pauses; `docker compose down` removes containers but keeps data. `docker compose down -v` deletes the `clickhouse_data` volume and your rows with it.
- Editing `.env` needs `docker compose up -d` to take effect — `restart` reuses the old environment.
- The producer starts from the current chain tip, not from genesis, so history begins when you start it. It has no offset checkpoint: transfers that land while it's stopped are skipped.
- Set `KAFKA_BROKER` to override the default `localhost:19092`.
- The dashboard is a Grafana Cloud instance querying this ClickHouse directly; it isn't part of the compose stack, so it needs ClickHouse reachable from outside your machine.
