"""Read-only export of the Pump Monitor research dataset (rows + labels).

Usage:
    DATABASE_PUBLIC_URL=... python -m scripts.export_pump_research \\
        --start 2026-10-01T00:00:00Z --end 2026-10-08T00:00:00Z --out pump_research.parquet
        [--label-set pump_research_labels_v1] [--symbols BTC_USDT,ETH_USDT]

One output row per (symbol, minute). ``vals`` / ``contributions`` are decoded
into ``v_<indicator>`` / ``c_<component>`` columns with the key order stored in
``pump_research_value_keys``; labels are flattened into ``ret_gross_<h>``,
``ret_net_<h>``, ``mfe_<h>``, ``mae_<h>``, ``t_peak_minutes`` and
``<set>_y`` / ``<set>_t_touch`` / ``<set>_r_at_exit``. ``.parquet`` needs pyarrow;
any other extension writes CSV. The session is read-only.
"""
import argparse
import asyncio
import json
import os
from datetime import datetime


def _flatten(row: dict, keys: dict) -> dict:
    out = {k: row[k] for k in row if k not in ("vals", "contributions", "returns", "path", "barriers",
                                                "categorical", "null_reasons", "value_keys_hash")}
    vk, ck = keys.get(row["value_keys_hash"], ([], []))
    for k, v in zip(vk, row["vals"] or []):
        out[f"v_{k}"] = v
    for k, v in zip(ck, row["contributions"] or []):
        out[f"c_{k}"] = v
    for k, v in (row.get("categorical") or {}).items():
        out[f"v_{k}"] = v
    out["null_reasons"] = json.dumps(row.get("null_reasons")) if row.get("null_reasons") else None
    for h, r in (row.get("returns") or {}).items():
        out[f"ret_gross_{h}"], out[f"ret_net_{h}"] = r.get("gross"), r.get("net")
    for h, p in (row.get("path") or {}).items():
        if isinstance(p, dict):
            out[f"mfe_{h}"], out[f"mae_{h}"] = p.get("mfe"), p.get("mae")
        else:
            out[h] = p
    for name, b in (row.get("barriers") or {}).items():
        out[f"{name}_y"], out[f"{name}_t_touch"], out[f"{name}_r_at_exit"] = b.get("y"), b.get("t_touch"), b.get("r_at_exit")
    return out


async def main(args) -> None:
    import asyncpg
    import pandas as pd

    url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_PUBLIC_URL or DATABASE_URL is required")
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"), timeout=20)
    for kind in ("json", "jsonb"):
        await conn.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        async with conn.transaction(readonly=True):
            keys = {r["value_keys_hash"]: (r["value_keys"], r["contribution_keys"])
                    for r in await conn.fetch("SELECT * FROM pump_research_value_keys")}
            symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()]
            rows = await conn.fetch("""
                SELECT m.*, l.label_set_version, l.labeled_at, l.price_t, l.cost_pct, l.fee_roundtrip_pct,
                       l.returns, l.path, l.barriers, l.reason AS label_reason
                  FROM pump_research_minute m
                  LEFT JOIN pump_research_labels l
                    ON l.symbol = m.symbol AND l.ts = m.ts AND l.label_set_version = $3
                 WHERE m.ts >= $1 AND m.ts < $2
                   AND COALESCE(m.categorical->>'_research_role', '') <> 'label_drain'
                   AND (cardinality($4::text[]) = 0 OR m.symbol = ANY($4::text[]))
                 ORDER BY m.ts, m.symbol
            """, datetime.fromisoformat(args.start.replace("Z", "+00:00")),
                datetime.fromisoformat(args.end.replace("Z", "+00:00")), args.label_set, symbols)
    finally:
        await conn.close()
    frame = pd.DataFrame([_flatten(dict(r), keys) for r in rows])
    if args.out.endswith(".parquet"):
        frame.to_parquet(args.out, index=False)
    else:
        frame.to_csv(args.out, index=False)
    print(f"{len(frame)} rows -> {args.out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--label-set", default="pump_research_labels_v1")
    p.add_argument("--symbols", default="")
    asyncio.run(main(p.parse_args()))
