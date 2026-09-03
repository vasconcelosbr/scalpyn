"""Fetch Gate-final 1m candles for the frozen 559-cohort, per symbol.

Read-only against Gate's public candlesticks endpoint. Writes only to local
JSONL files (new, separate population) -- never touches ohlcv/ohlcv_shadow/
ohlcv_live/shadow_trades.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
OUT_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
META_OUT = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m_meta.json"

GATE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
TIMEOUT_HORIZON_MIN = 1440
INTERVAL_SECONDS = 60
POINTS_PER_CALL = 1000

REPLAY_CONTRACT_VERSION = "shadow_trailing_replay_gate_final_v1"


def parse_iso(s):
    return datetime.fromisoformat(s)


def parse_gate_candle(candle):
    close = float(candle[2])
    quote_volume = float(candle[1])
    if len(candle) > 6 and candle[6] not in (None, ""):
        base_volume = float(candle[6])
    else:
        base_volume = quote_volume / close if close > 0 else 0.0
    closed_raw = candle[7] if len(candle) > 7 else None
    is_closed = None
    if isinstance(closed_raw, bool):
        is_closed = closed_raw
    elif closed_raw is not None:
        norm = str(closed_raw).strip().lower()
        if norm in ("true", "1"):
            is_closed = True
        elif norm in ("false", "0"):
            is_closed = False
    return {
        "time": datetime.fromtimestamp(int(candle[0]), tz=timezone.utc),
        "open": float(candle[5]),
        "high": float(candle[3]),
        "low": float(candle[4]),
        "close": close,
        "volume": base_volume,
        "quote_volume": quote_volume,
        "is_closed": is_closed,
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = json.load(open(COHORT, encoding="utf-8"))

    by_symbol = {}
    for r in rows:
        sym = r["symbol"]
        et = parse_iso(r["entry_timestamp"])
        by_symbol.setdefault(sym, {"min_entry": et, "max_entry": et})
        d = by_symbol[sym]
        if et < d["min_entry"]:
            d["min_entry"] = et
        if et > d["max_entry"]:
            d["max_entry"] = et

    now = datetime.now(timezone.utc)
    fetched_at = now.isoformat()

    meta = {
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "cohort_manifest_sha256": "b8c4e875521e2ddda06be79630f3b4d8cd7c5b6bae7f990db08b900cce9f6667",
        "fetched_at": fetched_at,
        "timeout_horizon_minutes": TIMEOUT_HORIZON_MIN,
        "symbols": {},
    }

    client = httpx.Client(timeout=httpx.Timeout(15.0, connect=10.0))
    total_calls = 0

    for sym, d in sorted(by_symbol.items()):
        window_start = d["min_entry"].replace(second=0, microsecond=0)
        window_end = d["max_entry"].replace(second=0, microsecond=0) + timedelta(
            minutes=TIMEOUT_HORIZON_MIN
        )
        window_end = min(window_end, now.replace(second=0, microsecond=0))

        start_ts = int(window_start.timestamp())
        end_ts = int(window_end.timestamp())
        expected_points = (end_ts - start_ts) // INTERVAL_SECONDS + 1

        all_records = {}
        cursor_ts = start_ts
        calls_for_symbol = 0
        errors = []
        while cursor_ts <= end_ts:
            page_end_ts = min(cursor_ts + (POINTS_PER_CALL - 1) * INTERVAL_SECONDS, end_ts)
            params = {
                "currency_pair": sym,
                "interval": "1m",
                "from": cursor_ts,
                "to": page_end_ts,
            }
            try:
                resp = client.get(GATE_URL, params=params)
                total_calls += 1
                calls_for_symbol += 1
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "5"))
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, list):
                    errors.append(f"unexpected payload type at cursor={cursor_ts}")
                    break
                for raw in payload:
                    parsed = parse_gate_candle(raw)
                    if parsed["is_closed"] is True:
                        all_records[parsed["time"]] = parsed
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc} at cursor={cursor_ts}")
                break
            cursor_ts = page_end_ts + INTERVAL_SECONDS
            time.sleep(0.25)

        out_path = os.path.join(OUT_DIR, f"{sym}.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for t in sorted(all_records):
                rec = all_records[t]
                f.write(
                    json.dumps(
                        {
                            "time": rec["time"].isoformat(),
                            "open": rec["open"],
                            "high": rec["high"],
                            "low": rec["low"],
                            "close": rec["close"],
                            "volume": rec["volume"],
                            "quote_volume": rec["quote_volume"],
                        }
                    )
                    + "\n"
                )

        received = len(all_records)
        meta["symbols"][sym] = {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "expected_points": expected_points,
            "received_points": received,
            "missing_points": expected_points - received,
            "calls": calls_for_symbol,
            "errors": errors,
            "n_trades": sum(1 for r in rows if r["symbol"] == sym),
        }
        print(
            f"{sym}: expected={expected_points} received={received} "
            f"missing={expected_points - received} calls={calls_for_symbol} "
            f"errors={errors}"
        )

    meta["total_calls"] = total_calls
    with open(META_OUT, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print("total_calls:", total_calls)
    print("wrote meta:", META_OUT)


if __name__ == "__main__":
    main()
