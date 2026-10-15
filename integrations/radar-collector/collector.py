"""One persisted-only GET per day; isolated SQLite archive, no trading imports."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID
from zoneinfo import ZoneInfo


class Unavailable(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable("redirect_refused")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise Unavailable("timestamp_without_timezone")
    return parsed


def validate(raw, watchlist_id, pool_id):
    def reject_constant(_):
        raise Unavailable("invalid_json_number")
    data = json.loads(raw, parse_constant=reject_constant)
    expected = {
        "contract_version": "radar_watchlist_v1",
        "served_by": "service_readonly", "read_mode": "persisted-only",
        "membership_basis": "persisted_active_rows",
        "watchlist_id": watchlist_id, "source_pool_id": pool_id,
    }
    if any(data.get(k) != v for k, v in expected.items()):
        raise Unavailable("contract_mismatch")
    timestamp(data["queried_at"])
    if data["last_scanned_at"] is not None:
        timestamp(data["last_scanned_at"])
    assets = data["assets"]
    if not isinstance(assets, list) or type(data["asset_count"]) is not int or data["asset_count"] != len(assets):
        raise Unavailable("asset_count_mismatch")
    ids, symbols = [], []
    for asset in assets:
        ids.append(str(UUID(asset["id"])))
        symbols.append(asset["symbol"])
        # Require the fields, but preserve nulls and historical values verbatim.
        for name in ("current_price", "price_change_24h", "volume_24h", "market_cap", "alpha_score",
                     "entered_at", "refreshed_at", "analysis_snapshot"):
            asset[name]
        if not isinstance(asset["indicator_readings"], list):
            raise Unavailable("invalid_indicators")
        for reading in asset["indicator_readings"]:
            if (reading["freshness"] != "not_assessed" or reading["source_table"] != "indicators"
                    or reading["selection"] != "latest_per_group_timeframe"):
                raise Unavailable("indicator_contract_mismatch")
            for name in ("timestamp", "age_seconds", "scheduler_group", "timeframe", "values"):
                reading[name]
    if len(set(ids)) != len(ids) or any(not isinstance(s, str) or not s for s in symbols) or symbols != sorted(symbols):
        raise Unavailable("invalid_asset_order_or_identity")
    return data


def fetch(url, key, timeout, max_bytes):
    req = Request(url, headers={"X-Scalpyn-Service-Key": key,
                               "Accept": "application/json", "User-Agent": "scalpyn-radar-collector/1"}, method="GET")
    try:
        response = build_opener(NoRedirect()).open(req, timeout=timeout)
    except HTTPError as exc:
        exc.close()
        raise Unavailable("http_unavailable") from None
    with response:
        cache = {v.strip().lower() for v in response.headers.get("Cache-Control", "").split(",")}
        if (response.status != 200 or response.headers.get("X-Scalpyn-Read-Mode") != "persisted-only"
                or not {"private", "no-store"}.issubset(cache)
                or response.headers.get_content_type() != "application/json"):
            raise Unavailable("http_contract_mismatch")
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise Unavailable("response_too_large")
    return raw


def database(path):
    db = sqlite3.connect(path, timeout=1, isolation_level=None)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS snapshots (watchlist_id TEXT NOT NULL, day TEXT NOT NULL, "
               "captured_at TEXT NOT NULL, sha256 TEXT NOT NULL, payload_gzip BLOB NOT NULL, "
               "PRIMARY KEY(watchlist_id, day))")
    return db


def unpack(row):
    raw = gzip.decompress(row[1])
    if hashlib.sha256(raw).hexdigest() != row[0]:
        raise Unavailable("archive_checksum_mismatch")
    return raw


def collect(env, now=None, getter=fetch):
    watchlist_id = str(UUID(env["SCALPYN_WATCHLIST_ID"]))
    pool_id = str(UUID(env["SCALPYN_POOL_ID"]))
    url, key = env["SCALPYN_RADAR_URL"], env["SCALPYN_SERVICE_KEY"]
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.port not in (None, 443)
            or parts.path != f"/api/integrations/radar/watchlists/{watchlist_id}/assets"
            or not key.strip() or key != key.strip()):
        raise Unavailable("invalid_configuration")
    timeout, limit = float(env.get("HTTP_TIMEOUT_SECONDS", "90")), int(env.get("MAX_RESPONSE_BYTES", "16777216"))
    if not 0 < timeout <= 300 or not 0 < limit <= 67108864:
        raise Unavailable("invalid_limits")
    root = Path(env.get("SNAPSHOT_DIR", "/data"))
    # Refuse Railway's ephemeral filesystem if the volume is missing/misconfigured.
    if env.get("RAILWAY_SERVICE_ID") and (env.get("RAILWAY_VOLUME_MOUNT_PATH") != str(root) or not os.path.ismount(root)):
        raise Unavailable("persistent_volume_missing")
    root.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo(env.get("SNAPSHOT_TIMEZONE", "America/Sao_Paulo"))).date().isoformat()
    db = database(root / "snapshots.sqlite3")
    try:
        # Serializes overlapping executions; transaction rollback leaves the last good snapshot intact.
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT sha256,payload_gzip FROM snapshots WHERE watchlist_id=? AND day=?",
                              (watchlist_id, day)).fetchone()
        if previous:
            raw = unpack(previous)
            data = validate(raw, watchlist_id, pool_id)
            outcome = "already_saved_verified"
        else:
            raw = getter(url, key, timeout, limit)
            data = validate(raw, watchlist_id, pool_id)
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                       (watchlist_id, day, now.isoformat(), hashlib.sha256(raw).hexdigest(), gzip.compress(raw, mtime=0)))
            db.execute("COMMIT")
            stored = db.execute("SELECT sha256,payload_gzip FROM snapshots WHERE watchlist_id=? AND day=?",
                                (watchlist_id, day)).fetchone()
            if unpack(stored) != raw:
                raise Unavailable("archive_readback_failed")
            outcome = "saved_verified"
        return {"event": outcome, "watchlist_id": watchlist_id, "day": day,
                "asset_count": data["asset_count"], "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "freshness": "not_assessed", "usable_for_trading": False}
    finally:
        if db.in_transaction:
            db.execute("ROLLBACK")
        db.close()


def export(root, watchlist_id, day, output):
    # Read-only connection: exports never create/modify the archive or contact Scalpyn.
    datetime.strptime(day, "%Y-%m-%d")
    db = sqlite3.connect((Path(root) / "snapshots.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = db.execute("SELECT sha256,payload_gzip FROM snapshots WHERE watchlist_id=? AND day=?",
                         (str(UUID(watchlist_id)), day)).fetchone()
        if row is None:
            raise Unavailable("snapshot_not_found")
        raw = unpack(row)
        with open(output, "xb") as target:
            target.write(raw)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-day")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.export_day:
            if not args.output:
                raise Unavailable("output_required")
            export(os.environ.get("SNAPSHOT_DIR", "/data"), os.environ["SCALPYN_WATCHLIST_ID"], args.export_day, args.output)
            result = {"event": "export_verified"}
        else:
            from delivery import run
            result = run(os.environ, collect)
        print(json.dumps(result), flush=True)
        return 0
    except Exception:
        # Neither exception messages nor HTTP bodies/headers/URLs are safe to log.
        print(json.dumps({"event": "collection_unavailable", "usable_for_trading": False}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
