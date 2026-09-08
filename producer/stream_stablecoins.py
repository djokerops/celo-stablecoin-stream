import os
import json
import time
from web3 import Web3
from confluent_kafka import Producer
from requests.exceptions import ConnectionError as ReqConnErr

RPC_URL      = "https://forno.celo.org"
KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "localhost:19092")
KAFKA_TOPIC  = "celo_stablecoins"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
POLL_SECONDS = 5

# symbol -> (address, decimals, peg_currency)
TOKENS = {
    "USDm":  ("0x765DE816845861e75A25fCA122bb6898B8B1282a", 18, "USD"),
    "EURm":  ("0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73", 18, "EUR"),
    "BRLm":  ("0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787", 18, "BRL"),
    "USDC":  ("0xcebA9300f2b948710d2653dD7B07f33A8B32118C",  6, "USD"),
    "USDT":  ("0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e",  6, "USD"),
    "AUDm":  ("0x7175504C455076F15c04A2F90a8e352281F492F9", 18, "AUD"),
    "CADm":  ("0xff4Ab19391af240c311c54200a492233052B6325", 18, "CAD"),
    "CHFm":  ("0xb55a79F398E759E43C95b979163f30eC87Ee131D", 18, "CHF"),
    "COPm":  ("0x8A567e2aE79CA692Bd748aB832081C45de4041eA", 18, "COP"),
    "GBPm":  ("0xCCF663b1fF11028f0b19058d0f7B674004a40746", 18, "GBP"),
    "GHSm":  ("0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313", 18, "GHS"),
    "JPYm":  ("0xc45eCF20f3CD864B32D9794d6f76814aE8892e20", 18, "JPY"),
    "KESm":  ("0x456a3D042C0DbD3db53D5489e98dFb038553B0d0", 18, "KES"),
    "NGNm":  ("0xE2702Bd97ee33c88c8f6f92DA3B733608aa76F71", 18, "NGN"),
    "PHPm":  ("0x105d4A9306D2E55a71d2Eb95B81553AE1dC20d7B", 18, "PHP"),
    "XOFm":  ("0x73F93dcc49cB8A239e2032663e9475dd5ef29A08", 18, "XOF"),
    "ZARm":  ("0x4c35853A3B4e647fD266f4de678dCc8fEC410BF6", 18, "ZAR"),
    "BRLA":  ("0xFECB3F7c54E2CAAE9dC6Ac9060A822D47E053760", 18, "BRL"),
    "VCHF":  ("0xC5ebEa9984C485EC5D58cA5a2D376620d93aF871", 18, "CHF"),
    "VGBP":  ("0x7aE4265eCFC1F31bc0E112DfCFe3D78E01f4BB7f", 18, "GBP"),
    "USDGLO": ("0x4F604735c1cF31399C6E711D5962b2B3E0225AD3", 18, "USD"),
    "USDM":  ("0x59D9356E565Ab3A36dD77763Fc0d87fEaf85508C", 18, "USD"),
    "cNGN":  ("0xF6829D7393dAe24509eb1E52eE8e572e2E271a4f", 18, "NGN"),
    "wARS":  ("0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d", 18, "ARS"),
    "wBRL":  ("0xd76f5faf6888e24d9f04bf92a0c8b921fe4390e0", 18, "BRL"),
    "wMXN":  ("0x337e7456b420bd3481e7fa61fa9850343d610d34", 18, "MXN"),
    "wCOP":  ("0x8a1d45e102e886510e891d2ec656a708991e2d76", 18, "COP"),
    "wPEN":  ("0x4F34c8b3b5FB6D98Da888F0feA543d4d9C9F2eBE", 18, "PEN"),
    "wCLP":  ("0x61D450a098b6a7f69fC4b98CE68198fe59768651", 18, "CLP"),


}
FALLBACK_USD = {"USD": 1.0, "EUR": 1.08, "BRL": 0.196, "AUD": 0.71, "CAD": 0.72,
                "CHF": 1.22, "COP": 0.00032, "GBP": 1.35, "GHS": 0.089, "JPY": 0.0064, "CLP": 0.00108, 
                "KES": 0.0077, "NGN": 0.0007, "PHP": 0.016, "XOF": 0.0018, "ZAR": 0.062, "ARS": 0.00066, "MXN": 0.05912, "PEN": 0.2981}

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 30}))
producer = Producer({"bootstrap.servers": KAFKA_BROKER})

# Mento SortedOracles (mainnet) — read median USD rate for a Mento token
SORTED_ORACLES = "0xefB84935239dAcdecF7c5bA76d8dE40b077B7b33"
ORACLE_ABI = [{
    "constant": True,
    "inputs": [{"name": "token", "type": "address"}],
    "name": "medianRate",
    "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}],
    "type": "function",
}]
oracle = w3.eth.contract(
    address=Web3.to_checksum_address(SORTED_ORACLES), abi=ORACLE_ABI
)

def usd_rate(symbol, address, peg):
    if peg == "USD":
        return 1.0

    fallback = FALLBACK_USD.get(peg, 1.0)
    try:
        num, den = oracle.functions.medianRate(
            Web3.to_checksum_address(address)
        ).call()
        if not den:
            return fallback

        raw_rate = num / den

        # Sanity bound: Mento feeds vary in scale/direction, so a raw
        # num/den is not always a clean USD price. Accept the oracle only
        # if it's within a sane band of our approximate peg; anything
        # wildly off (e.g. COPm reading ~233 vs ~0.00032) is a misread,
        # not real FX drift, so fall back to the reference peg.
        lo, hi = fallback * 0.5, fallback * 2.0
        if lo <= raw_rate <= hi:
            return raw_rate

        print(f"  {symbol}: oracle rate {raw_rate:.6g} outside "
              f"[{lo:.6g}, {hi:.6g}]; using fallback {fallback}")
        return fallback

    except Exception as e:
        print(f"  oracle read failed for {symbol} ({e}); using fallback")
        return fallback

def delivery_report(err, msg):
    if err is not None:
        print(f"  delivery FAILED: {err}")

def process_block(bn, block_ts):
    for symbol, (addr, decimals, peg) in TOKENS.items():
        logs = w3.eth.get_logs({
            "address": Web3.to_checksum_address(addr),
            "topics": [TRANSFER_TOPIC],
            "fromBlock": bn, "toBlock": bn,
        })
        if not logs:
            continue
        rate = usd_rate(symbol, addr, peg)
        for lg in logs:
            frm = "0x" + lg["topics"][1].hex()[-40:]
            to  = "0x" + lg["topics"][2].hex()[-40:]
            raw = int(lg["data"].hex(), 16) if lg["data"] else 0
            amount_token = raw / (10 ** decimals)         # per-token decimals
            event = {
                "symbol": symbol, "peg": peg,
                "tx_hash": lg["transactionHash"].hex(),
                "log_index": lg["logIndex"],
                "block_number": bn, "block_timestamp": block_ts,
                "from_address": frm.lower(), "to_address": to.lower(),
                "amount_token": amount_token,
                "usd_rate": rate,
                "amount_usd": amount_token * rate,
            }
            producer.produce(
                KAFKA_TOPIC, key=event["tx_hash"],
                value=json.dumps(event), callback=delivery_report,
            )
        producer.flush()
        print(f"  block {bn:,} {symbol}: {len(logs)} transfers @ {rate:.4f} USD")

def run():
    while True:
        try:
            last = w3.eth.block_number
            print(f"Streaming from block {last:,}. Ctrl+C to stop.")
            break
        except Exception as e:
            print(f"  RPC unreachable ({type(e).__name__}); retry 10s"); time.sleep(10)

    while True:
        try:
            tip = w3.eth.block_number
            if tip <= last:
                time.sleep(POLL_SECONDS); continue
            for bn in range(last + 1, tip + 1):
                ts = w3.eth.get_block(bn)["timestamp"]
                process_block(bn, ts)
            last = tip
            time.sleep(POLL_SECONDS)
        except ReqConnErr:
            print("  network blip; retry 10s"); time.sleep(10)
        except Exception as e:
            print(f"  error: {e}; retry 10s"); time.sleep(10)

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        producer.flush(); print("\nStopped.")