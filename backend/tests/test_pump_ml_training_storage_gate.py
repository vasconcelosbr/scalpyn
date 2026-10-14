import asyncio
from uuid import UUID

from app.services import pump_ml_daily as daily

BUDGET = 10_000_000_000
COLLECTION_TABLES = ("pump_opportunity_observations", "pump_opportunity_labels", "pump_opportunity_price_paths",
                     "pump_opportunity_label_queue", "pump_opportunity_job_runs")


class DB:
    """Collection tables fill the whole budget; the ML ledger is tiny."""

    def __init__(self, ledger_bytes):
        self.ledger_bytes = ledger_bytes
        self.storage_sql = []

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            return True
        if "SELECT run_id" in sql:
            return None
        if "SELECT config_json" in sql:
            return {"enabled": True, "training_job_enabled": True, "budget": {"max_storage_bytes": BUDGET}}
        if "pg_total_relation_size" in sql:
            self.storage_sql.append(sql)
            return BUDGET + 433_152 if any(t in sql for t in COLLECTION_TABLES) else self.ledger_bytes
        return None

    async def execute(self, sql, *args):
        return None


def _run(monkeypatch, ledger_bytes):
    async def no_support(*args, **kwargs):
        return []
    monkeypatch.setattr(daily, "run_directional_horizons", no_support)
    db = DB(ledger_bytes)
    out = asyncio.run(daily.run_owner(db, UUID(int=1), horizons=[15]))
    return db, out


def test_full_collection_budget_does_not_block_scheduled_training(monkeypatch):
    db, out = _run(monkeypatch, ledger_bytes=1_500_000)
    assert out["reason"] != "pump_storage_budget_exhausted"
    assert out["reason"] == "no_horizon_with_sufficient_directional_support"
    assert db.storage_sql and not any(t in db.storage_sql[0] for t in COLLECTION_TABLES)


def test_training_still_blocked_when_its_own_ledger_is_full(monkeypatch):
    _, out = _run(monkeypatch, ledger_bytes=BUDGET)
    assert out["status"] == "blocked" and out["reason"] == "pump_storage_budget_exhausted"
