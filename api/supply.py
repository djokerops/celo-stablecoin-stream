"""
Total on-chain supply of every tracked stablecoin, priced in USD.

Read straight from Celo - totalSupply() per token over forno - so it never
touches ClickHouse. Pricing mirrors the producer exactly (Mento SortedOracles
median, accepted only within 0.5x-2x of a reference rate; else live FX; else
the hardcoded backstop), so the supply tile and the volume figures agree on
what a token is worth.

Runnable on its own for a per-token breakdown:

    python api/supply.py
"""

import json
import time
import urllib.request

RPC_URL = "https://forno.celo.org"
FX_URL  = "https://open.er-api.com/v6/latest/USD"
SORTED_ORACLES = "0xefB84935239dAcdecF7c5bA76d8dE40b077B7b33"

SEL_TOTAL_SUPPLY = "0x18160ddd"   # totalSupply()
SEL_MEDIAN_RATE  = "0xef90e1b0"   # medianRate(address)

# COPY of TOKENS in producer/stream_stablecoins.py - the two images build from
# separate contexts, so they can't share a module. Add/remove a token in BOTH.
# symbol -> (address, decimals, peg_currency)
TOKENS = {
    "USDm":   ("0x765DE816845861e75A25fCA122bb6898B8B1282a", 18, "USD"),
    "EURm":   ("0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73", 18, "EUR"),
    "BRLm":   ("0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787", 18, "BRL"),
    "USDC":   ("0xcebA9300f2b948710d2653dD7B07f33A8B32118C",  6, "USD"),
    "USDT":   ("0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e",  6, "USD"),
    "AUDm":   ("0x7175504C455076F15c04A2F90a8e352281F492F9", 18, "AUD"),
    "CADm":   ("0xff4Ab19391af240c311c54200a492233052B6325", 18, "CAD"),
    "CHFm":   ("0xb55a79F398E759E43C95b979163f30eC87Ee131D", 18, "CHF"),
    "COPm":   ("0x8A567e2aE79CA692Bd748aB832081C45de4041eA", 18, "COP"),
    "GBPm":   ("0xCCF663b1fF11028f0b19058d0f7B674004a40746", 18, "GBP"),
    "GHSm":   ("0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313", 18, "GHS"),
    "JPYm":   ("0xc45eCF20f3CD864B32D9794d6f76814aE8892e20", 18, "JPY"),
    "KESm":   ("0x456a3D042C0DbD3db53D5489e98dFb038553B0d0", 18, "KES"),
    "NGNm":   ("0xE2702Bd97ee33c88c8f6f92DA3B733608aa76F71", 18, "NGN"),
    "PHPm":   ("0x105d4A9306D2E55a71d2Eb95B81553AE1dC20d7B", 18, "PHP"),
    "XOFm":   ("0x73F93dcc49cB8A239e2032663e9475dd5ef29A08", 18, "XOF"),
    "ZARm":   ("0x4c35853A3B4e647fD266f4de678dCc8fEC410BF6", 18, "ZAR"),
    "BRLA":   ("0xFECB3F7c54E2CAAE9dC6Ac9060A822D47E053760", 18, "BRL"),
    "VCHF":   ("0xC5ebEa9984C485EC5D58cA5a2D376620d93aF871", 18, "CHF"),
    "VGBP":   ("0x7aE4265eCFC1F31bc0E112DfCFe3D78E01f4BB7f", 18, "GBP"),
    "USDGLO": ("0x4F604735c1cF31399C6E711D5962b2B3E0225AD3", 18, "USD"),
    "USDM":   ("0x59D9356E565Ab3A36dD77763Fc0d87fEaf85508C", 18, "USD"),
    "cNGN":   ("0xF6829D7393dAe24509eb1E52eE8e572e2E271a4f",  6, "NGN"),
    "wARS":   ("0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d", 18, "ARS"),
    "wBRL":   ("0xd76f5faf6888e24d9f04bf92a0c8b921fe4390e0", 18, "BRL"),
    "wMXN":   ("0x337e7456b420bd3481e7fa61fa9850343d610d34", 18, "MXN"),
    "wCOP":   ("0x8a1d45e102e886510e891d2ec656a708991e2d76", 18, "COP"),
    "wPEN":   ("0x4F34c8b3b5FB6D98Da888F0feA543d4d9C9F2eBE", 18, "PEN"),
    "wCLP":   ("0x61D450a098b6a7f69fC4b98CE68198fe59768651", 18, "CLP"),
    "USAT":   ("0xD2ab3C9A02DBBAB236BfEC45D1d755DF4267F771", 6, "USD"),
    "IDRX":   ("0x18Bc5bcC660cf2B9cE3cd51a404aFe1a0cBD3C22", 2, "IDR"),
}

# COPY of HARDCODED_USD in the producer - backstop if live FX lacks a currency.
HARDCODED_USD = {"USD": 1.0, "EUR": 1.08, "BRL": 0.196, "AUD": 0.71, "CAD": 0.72,
                 "CHF": 1.22, "COP": 0.00032, "GBP": 1.35, "GHS": 0.089, "JPY": 0.0064,
                 "CLP": 0.00108, "KES": 0.0077, "NGN": 0.0007, "PHP": 0.016, "XOF": 0.0018,
                 "ZAR": 0.062, "ARS": 0.00066, "MXN": 0.05912, "PEN": 0.2981, "IDR": 0.0000565}

TIMEOUT = 15
FX_TTL = 86400        # source updates daily
SUPPLY_TTL = 300      # supply moves slowly; one RPC round per 5 min is plenty

_fx = {"rates": {}, "at": 0.0}
_cache = {"data": None, "at": 0.0}


def _http_json(url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "celoflow-supply"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _fx_rate(peg):
    """Live FX (CCY -> USD), refreshed daily; hardcoded backstop otherwise."""
    if time.time() - _fx["at"] > FX_TTL:
        try:
            data = _http_json(FX_URL)
            if data.get("result") == "success" and data.get("rates"):
                _fx["rates"] = {c: 1.0 / r for c, r in data["rates"].items() if r}
                _fx["at"] = time.time()
        except Exception as e:
            print(f"  FX refresh failed ({e}); keeping existing/hardcoded rates")
    return _fx["rates"].get(peg) or HARDCODED_USD.get(peg, 1.0)


def _eth_call_batch(calls):
    """calls: list of (to, data). One batched JSON-RPC request; returns the raw
    hex results in order, or None where that call errored."""
    payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"]}
               for i, (to, data) in enumerate(calls)]
    resp = _http_json(RPC_URL, payload)
    by_id = {r["id"]: r.get("result") for r in resp}
    return [by_id.get(i) for i in range(len(calls))]


def fetch_supply():
    """Read every token's totalSupply and price it. Raises if the supply batch
    itself fails; a single token's failure is reported per-row, not fatal."""
    symbols = list(TOKENS)
    calls = [(TOKENS[s][0], SEL_TOTAL_SUPPLY) for s in symbols]

    # Oracle reads only for non-USD pegs, same batch.
    non_usd = [s for s in symbols if TOKENS[s][2] != "USD"]
    calls += [(SORTED_ORACLES,
               SEL_MEDIAN_RATE + TOKENS[s][0].lower()[2:].rjust(64, "0"))
              for s in non_usd]

    results = _eth_call_batch(calls)
    supplies = dict(zip(symbols, results[:len(symbols)]))
    oracle = dict(zip(non_usd, results[len(symbols):]))

    tokens, total = [], 0.0
    for s in symbols:
        addr, decimals, peg = TOKENS[s]
        raw = supplies[s]
        if not raw or raw == "0x":
            tokens.append({"symbol": s, "peg": peg, "supply": None,
                           "usd_rate": None, "supply_usd": None})
            continue
        supply = int(raw, 16) / (10 ** decimals)
        rate = 1.0 if peg == "USD" else _usd_rate(s, peg, oracle.get(s))
        supply_usd = supply * rate
        total += supply_usd
        tokens.append({"symbol": s, "peg": peg, "supply": supply,
                       "usd_rate": rate, "supply_usd": round(supply_usd, 2)})

    tokens.sort(key=lambda t: t["supply_usd"] or 0, reverse=True)
    return {"total_usd": round(total, 2), "as_of": int(time.time()),
            "tokens": tokens}


def _usd_rate(symbol, peg, raw):
    """Producer's rule: take the oracle median only if it lands within 0.5x-2x
    of the reference rate - Mento feeds vary in scale/direction, so anything
    wildly off is a misread, not real FX drift."""
    fallback = _fx_rate(peg)
    if not raw or len(raw) < 130:
        return fallback
    num, den = int(raw[2:66], 16), int(raw[66:130], 16)
    if not den:
        return fallback
    rate = num / den
    return rate if fallback * 0.5 <= rate <= fallback * 2.0 else fallback


def get_supply():
    """Cached for SUPPLY_TTL. On an RPC failure keep serving the last good
    value; raise only when there has never been one."""
    if _cache["data"] and time.time() - _cache["at"] < SUPPLY_TTL:
        return _cache["data"]
    try:
        _cache["data"] = fetch_supply()
        _cache["at"] = time.time()
    except Exception as e:
        if not _cache["data"]:
            raise
        print(f"  supply refresh failed ({e}); serving last good value")
    return _cache["data"]


if __name__ == "__main__":
    d = fetch_supply()
    print(f"{'symbol':8} {'supply':>22} {'usd_rate':>12} {'supply_usd':>18}")
    for t in d["tokens"]:
        if t["supply"] is None:
            print(f"{t['symbol']:8} {'(call failed)':>22}")
            continue
        print(f"{t['symbol']:8} {t['supply']:>22,.2f} {t['usd_rate']:>12.6g} "
              f"{t['supply_usd']:>18,.2f}")
    print(f"\nTOTAL    ${d['total_usd']:,.2f}  ({len(d['tokens'])} tokens)")
