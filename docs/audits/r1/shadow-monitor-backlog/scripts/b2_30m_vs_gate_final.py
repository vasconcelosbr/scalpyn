import psycopg2
import httpx
import time
from datetime import datetime, timedelta, timezone

conn = psycopg2.connect(
    "postgresql://postgres:pfVYvunFISWEeWAytUNApAAbxtsNcEHM@zephyr.proxy.rlwy.net:23422/railway"
)
conn.set_session(readonly=True)
cur = conn.cursor()

cur.execute("""
    SELECT symbol, time, open, high, low, close, volume, quote_volume
    FROM ohlcv_shadow
    WHERE timeframe='30m' AND capture_contract_version='gate_ohlcv_state_v3'
    ORDER BY random()
    LIMIT 50
""")
sample = cur.fetchall()
print("sample size:", len(sample))

GATE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
client = httpx.Client(timeout=httpx.Timeout(15.0, connect=10.0))

def parse_candle(raw):
    close = float(raw[2])
    qv = float(raw[1])
    bv = float(raw[6]) if len(raw) > 6 and raw[6] not in (None, "") else (qv/close if close > 0 else 0.0)
    return {"open": float(raw[5]), "high": float(raw[3]), "low": float(raw[4]), "close": close,
            "volume": bv, "quote_volume": qv}

exact = 0
diverge = []
errors = 0
for symbol, t, o, h, l, c, v, qv in sample:
    ts = int(t.timestamp())
    params = {"currency_pair": symbol, "interval": "30m", "from": ts - 1800, "to": ts + 1800}
    try:
        resp = client.get(GATE_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
        match = None
        for raw in payload:
            if int(raw[0]) == ts:
                match = parse_candle(raw)
                break
        if match is None:
            errors += 1
            print("NO GATE MATCH", symbol, t)
            continue
        shadow_vals = {"open": float(o), "high": float(h), "low": float(l), "close": float(c),
                        "volume": float(v), "quote_volume": float(qv)}
        diffs = {}
        for field in shadow_vals:
            a, b = shadow_vals[field], match[field]
            if abs(a - b) > 1e-8:
                diffs[field] = (a, b, a - b)
        if diffs:
            diverge.append((symbol, t, diffs))
        else:
            exact += 1
    except Exception as exc:
        errors += 1
        print("ERROR", symbol, t, exc)
    time.sleep(0.25)

print("\nexact matches:", exact, "/", len(sample) - errors, " errors:", errors)
print("divergences:", len(diverge))
for symbol, t, diffs in diverge:
    print(symbol, t, diffs)
