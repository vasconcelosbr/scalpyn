import base64
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("archive_contract", ROOT.parent / "radar-collector" / "collector.py")
contract = importlib.util.module_from_spec(spec)
sys.modules["archive_contract"] = contract
spec.loader.exec_module(contract)
import app as dashboard

WL = "68292ef9-c711-453e-be7c-46c1de10e9d4"
POOL = "2f5c6c1a-5ee6-4756-b039-7d5695f54508"
OWNER = "8080110c-ee9d-4a2b-a53f-6bef86dd8867"


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {"SNAPSHOT_DIR": self.tmp.name, "PUBLIC_ORIGIN": "https://dashboard.test",
               "SCALPYN_AUTH_ORIGIN": "https://scalpyn.vercel.app", "DASHBOARD_OWNER_ID": OWNER,
               "INGEST_KEY_SHA256": hashlib.sha256(b"test-ingest").hexdigest(),
               "SCALPYN_WATCHLIST_ID": WL, "SCALPYN_POOL_ID": POOL}
        self.patch = patch.dict(os.environ, env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        dashboard.sessions.clear()
        dashboard.attempts.clear()
        self.client = TestClient(dashboard.app, base_url="https://dashboard.test")
        self.addCleanup(self.client.close)
        self.headers = {"X-Radar-Ingest-Key": "test-ingest"}
        self.payload = dict(contract_version="radar_watchlist_v1", served_by="service_readonly", read_mode="persisted-only",
            membership_basis="persisted_active_rows", watchlist_id=WL, source_pool_id=POOL,
            queried_at="2026-10-15T20:00:00+00:00", last_scanned_at=None, asset_count=0, assets=[])
        self.raw = json.dumps(self.payload).encode()
        self.body = {"day": "2026-10-15", "captured_at": "2026-10-15T20:01:00+00:00",
            "sha256": hashlib.sha256(self.raw).hexdigest(), "payload_gzip_b64": base64.b64encode(gzip.compress(self.raw)).decode()}

    def login(self, owner=OWNER):
        with patch.object(dashboard, "auth_request", side_effect=[{"access_token": "never-return-this"}, {"id": owner, "name": "Operator"}]) as auth:
            response = self.client.post("/api/login", headers={"Origin": "https://dashboard.test"},
                                        json={"email": "test@example.test", "password": "never-return-this"})
        return response

    def ingest(self):
        return self.client.post("/internal/snapshot", headers=self.headers, json=self.body)

    def test_fail_closed_without_session_and_keys(self):
        for path in ("/api/overview", "/api/snapshots/2026-10-15", "/internal/catalog"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/internal/snapshot", json=self.body).status_code, 401)
        self.assertEqual(self.client.get("/api/overview", headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get("/api/overview", headers={"Authorization":"Bearer invalid"}).status_code,401)

    def test_login_origin_owner_session_logout(self):
        self.assertEqual(self.client.post("/api/login",json={"email":"test@example.test","password":"x"}).status_code,403)
        self.assertEqual(self.login(WL).status_code,403)
        response=self.login()
        self.assertEqual(response.status_code,200)
        self.assertNotIn("never-return-this",response.text)
        cookie=response.headers["set-cookie"]
        for flag in ("HttpOnly","Secure","SameSite=strict","Path=/"):
            self.assertIn(flag,cookie)
        self.assertEqual(self.client.get("/api/session").status_code,200)
        self.client.post("/api/logout",headers={"Origin":"https://dashboard.test"})
        self.assertEqual(self.client.get("/api/session").status_code,401)

    def test_archive_roundtrip_immutable_and_ui_read_only(self):
        self.assertEqual(self.ingest().status_code,200)
        self.assertEqual(self.ingest().status_code,200)
        self.login()
        path=Path(self.tmp.name)/"dashboard.sqlite3"
        before=path.read_bytes()
        self.assertEqual(self.client.get("/api/snapshots/2026-10-15").content,self.raw)
        overview=self.client.get("/api/overview").json()
        self.assertEqual(overview["snapshots"][0]["asset_count"],0)
        self.assertIsNone(overview["snapshots"][0]["last_scanned_at"])
        self.assertEqual(path.read_bytes(),before)
        other=json.dumps(dict(self.payload,last_scanned_at="2020-01-01T00:00:00Z")).encode()
        self.body.update(sha256=hashlib.sha256(other).hexdigest(),payload_gzip_b64=base64.b64encode(gzip.compress(other)).decode())
        self.assertEqual(self.ingest().status_code,409)
        self.assertEqual(self.client.get("/api/snapshots/2026-10-15").content,self.raw)

    def test_integrity_limits_and_identity(self):
        self.body["sha256"]="0"*64
        self.assertEqual(self.ingest().status_code,422)
        raw=json.dumps(dict(self.payload,watchlist_id=POOL)).encode()
        self.body.update(sha256=hashlib.sha256(raw).hexdigest(),payload_gzip_b64=base64.b64encode(gzip.compress(raw)).decode())
        self.assertEqual(self.ingest().status_code,422)
        bomb=b"a"*16777217
        self.body.update(sha256=hashlib.sha256(bomb).hexdigest(),payload_gzip_b64=base64.b64encode(gzip.compress(bomb)).decode())
        self.assertEqual(self.ingest().status_code,422)
        self.assertEqual(self.client.post("/api/login",content=b"a"*16385).status_code,413)

    def test_expired_session_headers_and_no_mutations(self):
        self.ingest();self.login()
        for record in dashboard.sessions.values(): record["expires"]=0
        response=self.client.get("/api/overview")
        self.assertEqual(response.status_code,401)
        self.assertIn("no-store",response.headers["cache-control"])
        self.assertIn("frame-ancestors 'none'",response.headers["content-security-policy"])
        self.assertEqual(self.client.post("/api/collect").status_code,404)
        self.assertEqual(self.client.delete("/api/snapshots/2026-10-15").status_code,405)

    def test_run_lifecycle_cannot_rewrite_terminal(self):
        body=dict(id=POOL,started_at="2026-10-15T20:00:00Z",finished_at=None,status="running")
        self.assertEqual(self.client.post("/internal/run",headers=self.headers,json=body).status_code,200)
        body.update(finished_at="2026-10-15T20:01:00Z",status="collection_failed")
        self.assertEqual(self.client.post("/internal/run",headers=self.headers,json=body).status_code,200)
        body.update(finished_at=None,status="running")
        self.client.post("/internal/run",headers=self.headers,json=body)
        self.login()
        self.assertEqual(self.client.get("/api/overview").json()["runs"][0]["status"],"collection_failed")


if __name__ == "__main__": unittest.main()
