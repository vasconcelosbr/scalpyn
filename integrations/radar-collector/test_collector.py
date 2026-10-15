from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone, timedelta
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

import collector as c

WL = "68292ef9-c711-453e-be7c-46c1de10e9d4"
POOL = "2f5c6c1a-5ee6-4756-b039-7d5695f54508"
NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
PAYLOAD = dict(contract_version="radar_watchlist_v1", served_by="service_readonly", read_mode="persisted-only",
               membership_basis="persisted_active_rows", watchlist_id=WL, source_pool_id=POOL,
               queried_at=NOW.isoformat(), last_scanned_at=None, asset_count=0, assets=[])


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(SCALPYN_WATCHLIST_ID=WL, SCALPYN_POOL_ID=POOL, SCALPYN_SERVICE_KEY="test-only-secret",
                        SCALPYN_RADAR_URL=f"https://example.test/api/integrations/radar/watchlists/{WL}/assets",
                        SNAPSHOT_DIR=self.tmp.name)
        self.raw = json.dumps(PAYLOAD).encode()

    def run_collect(self, raw=None, now=NOW):
        return c.collect(self.env, now, lambda *args: self.raw if raw is None else raw)

    def test_empty_and_independent_process_readback_export(self):
        saved = self.run_collect()
        with patch.object(c, "fetch", side_effect=AssertionError("no fetch")):
            second = c.collect(self.env, NOW, lambda *args: self.fail("must not fetch twice"))
        self.assertEqual(second["event"], "already_saved_verified")
        self.assertEqual(saved["sha256"], second["sha256"])
        output = Path(self.tmp.name) / "snapshot.json"
        c.export(self.tmp.name, WL, "2026-10-15", output)
        self.assertEqual(output.read_bytes(), self.raw)
        self.assertFalse(saved["usable_for_trading"])

    def test_stale_nulls_decision_and_latest_remain_verbatim(self):
        data = deepcopy(PAYLOAD)
        data["last_scanned_at"] = "2020-01-01T00:00:00+00:00"
        data["assets"] = [dict(id=POOL, symbol="AAA_USDT", current_price=None, price_change_24h=None,
            volume_24h=0, market_cap=None, alpha_score=17, entered_at=None, refreshed_at=None,
            analysis_snapshot={"historical_score": 12}, indicator_readings=[dict(selection="latest_per_group_timeframe",
            source_table="indicators", scheduler_group=None, timeframe="1m", timestamp=None,
            age_seconds=None, freshness="not_assessed", values={"rsi": None})])]
        data["asset_count"] = 1
        raw = json.dumps(data).encode()
        self.run_collect(raw)
        output = Path(self.tmp.name) / "snapshot.json"
        c.export(self.tmp.name, WL, "2026-10-15", output)
        self.assertEqual(output.read_bytes(), raw)

    def test_bad_contract_does_not_write_snapshot(self):
        for key, value in [("contract_version", "wrong"), ("asset_count", 1), ("source_pool_id", WL),
                           ("watchlist_id", POOL), ("read_mode", "live"), ("asset_count", False)]:
            with self.subTest(key=key):
                data = dict(PAYLOAD, **{key: value})
                with self.assertRaises(c.Unavailable):
                    self.run_collect(json.dumps(data).encode())
        with closing(sqlite3.connect(Path(self.tmp.name) / "snapshots.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)

    def test_failure_preserves_previous_day(self):
        saved = self.run_collect()
        with self.assertRaises(TimeoutError):
            c.collect(self.env, NOW + timedelta(days=1), lambda *args: (_ for _ in ()).throw(TimeoutError()))
        self.assertEqual(self.run_collect()["sha256"], saved["sha256"])

    def test_corruption_is_not_overwritten(self):
        self.run_collect()
        with closing(sqlite3.connect(Path(self.tmp.name) / "snapshots.sqlite3")) as db:
            db.execute("UPDATE snapshots SET payload_gzip=?", (gzip.compress(b"corrupt"),))
            db.commit()
        with self.assertRaises(c.Unavailable):
            self.run_collect()

    def test_missing_volume_and_unsafe_url_fail_before_network(self):
        for changes in [dict(RAILWAY_SERVICE_ID="test"), dict(SCALPYN_SERVICE_KEY=""),
                        dict(SCALPYN_RADAR_URL="http://example.test"),
                        dict(SCALPYN_RADAR_URL=f"https://example.test/api/watchlists/{WL}/assets")]:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(c.Unavailable):
                    c.collect(dict(self.env, **changes), NOW, lambda *args: self.fail("no network"))

    def test_storage_failure_and_overlap_fail_closed(self):
        with patch.object(c, "database", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_collect()
        db = c.database(Path(self.tmp.name) / "snapshots.sqlite3")
        try:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                self.run_collect()
        finally:
            db.close()

    def test_main_redacts_errors(self):
        with patch.object(c, "collect", side_effect=RuntimeError("secret and HTTP body")), \
                patch("sys.argv", ["collector.py"]), patch("builtins.print") as printer:
            self.assertEqual(c.main(), 1)
        self.assertNotIn("secret", str(printer.call_args))

    def test_http_auth_headers_limits_and_redirect_refusal(self):
        seen = []
        raw = self.raw
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                seen.append((self.path, self.headers.get("X-Scalpyn-Service-Key"), self.headers.get("Authorization")))
                code = 302 if self.path == "/redirect" else 401 if self.path == "/unauthorized" else 200
                self.send_response(code)
                self.send_header("Location", "/should-never-be-called")
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Scalpyn-Read-Mode", "persisted-only")
                self.end_headers()
                self.wfile.write(raw)
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            self.assertEqual(c.fetch(base, "test-key", 2, 10000), raw)
            with self.assertRaises(c.Unavailable):
                c.fetch(base + "/redirect", "test-key", 2, 10000)
            with self.assertRaises(c.Unavailable):
                c.fetch(base, "test-key", 2, 1)
            with self.assertRaises(Exception):
                c.fetch(base + "/unauthorized", "test-key", 2, 10000)
            self.assertTrue(all(key == "test-key" and jwt is None for _, key, jwt in seen))
            self.assertNotIn("/should-never-be-called", [path for path, _, _ in seen])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
