"""Optional replication to the isolated dashboard; never affects source snapshots."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from uuid import uuid4

from collector import NoRedirect, unpack


def request(env, path, body=None):
    base = env["DASHBOARD_INGEST_URL"].rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("Invalid dashboard origin")
    key = env["DASHBOARD_INGEST_KEY"]
    if not key.strip():
        raise ValueError("Missing ingestion key")
    data = None if body is None else json.dumps(body).encode()
    req = Request(base + path, data=data, headers={"X-Radar-Ingest-Key": key, "Content-Type": "application/json"},
                  method="GET" if data is None else "POST")
    with build_opener(NoRedirect()).open(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("Dashboard unavailable")
        return json.loads(response.read(1048576))


def sync(env):
    known = request(env, "/internal/catalog")["snapshots"]
    path = (Path(env.get("SNAPSHOT_DIR", "/data")) / "snapshots.sqlite3").resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        for day, captured, digest, compressed in db.execute(
                "SELECT day,captured_at,sha256,payload_gzip FROM snapshots WHERE watchlist_id=? ORDER BY day",
                (env["SCALPYN_WATCHLIST_ID"],)):
            if day in known:
                if known[day] != digest:
                    raise RuntimeError("Replica conflict")
                continue
            unpack((digest, compressed))
            receipt = request(env, "/internal/snapshot", {"day": day, "captured_at": captured, "sha256": digest,
                    "payload_gzip_b64": base64.b64encode(compressed).decode()})
            if receipt.get("verified") is not True or receipt.get("sha256") != digest:
                raise RuntimeError("Replica receipt mismatch")
    finally:
        db.close()


def run(env, collect):
    run_id, started = str(uuid4()), datetime.now(timezone.utc).isoformat()
    enabled = bool(env.get("DASHBOARD_INGEST_URL"))
    def report(status):
        request(env, "/internal/run", {"id": run_id, "started_at": started,
                "finished_at": None if status == "running" else datetime.now(timezone.utc).isoformat(), "status": status})
    if enabled:
        try:
            report("running")
        except Exception:
            pass  # A dashboard outage must not prevent the actual archive operation.
    try:
        result = collect(env)
    except Exception:
        if enabled:
            try:
                report("collection_failed")
            except Exception:
                pass
        raise
    if enabled:
        try:
            sync(env)
            report(result["event"])
            result["dashboard_sync"] = "verified"
        except Exception:
            try:
                report("delivery_failed")
            except Exception:
                pass
            # The caller exits nonzero, but the successfully saved snapshot remains.
            raise RuntimeError("Dashboard synchronization unavailable") from None
    return result
