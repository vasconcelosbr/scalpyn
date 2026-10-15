import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import delivery
from test_collector import WL, POOL, NOW, PAYLOAD
import collector
import json


class DeliveryTests(unittest.TestCase):
    def test_sync_exact_archive_and_only_missing_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(SNAPSHOT_DIR=tmp,SCALPYN_WATCHLIST_ID=WL,SCALPYN_POOL_ID=POOL,SCALPYN_SERVICE_KEY="test",
                     SCALPYN_RADAR_URL=f"https://example.test/api/integrations/radar/watchlists/{WL}/assets")
            result=collector.collect(env,NOW,lambda *args:json.dumps(PAYLOAD).encode())
            with patch.object(delivery,"request",side_effect=[{"snapshots":{}},{"verified":True,"sha256":result["sha256"]}]) as req:
                delivery.sync(env)
                self.assertEqual(req.call_args_list[1].args[2]["sha256"],result["sha256"])
            with patch.object(delivery,"request",return_value={"snapshots":{"2026-10-15":result["sha256"]}}) as req:
                delivery.sync(env);self.assertEqual(req.call_count,1)

    def test_dashboard_outage_does_not_prevent_collection(self):
        calls=[]
        def collect(env):calls.append(True);return {"event":"saved_verified"}
        with patch.object(delivery,"request",side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):delivery.run({"DASHBOARD_INGEST_URL":"https://example.test"},collect)
        self.assertEqual(calls,[True])

    def test_unconfigured_dashboard_preserves_existing_behavior(self):
        with patch.object(delivery,"request",side_effect=AssertionError("no network")):
            self.assertEqual(delivery.run({},lambda env:{"event":"saved_verified"}),{"event":"saved_verified"})


if __name__=="__main__":unittest.main()
