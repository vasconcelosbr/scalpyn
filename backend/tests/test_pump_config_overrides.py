"""v1.17 (2026-10-08): pump_opportunity saves persist only overrides, so default changes
reach users; migration 240 unfreezes candle defaults pinned by the old full-config writes."""
import asyncio
import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path

from app.services import pump_opportunity_engine as eng
from app.services import pump_opportunity_service as svc


def test_overrides_round_trip_and_keep_only_real_changes():
    full = eng.config(None)
    stored = eng.overrides(full)
    assert set(stored) <= set(eng.PERSIST_ALWAYS)            # defaults only → just the anchor flags
    assert eng.config(stored) == full
    changed = deepcopy(full)
    changed["research"]["candle"]["max_rows"] = 70000
    changed["enabled"] = True
    s = eng.overrides(changed)
    assert s["research"] == {"candle": {"max_rows": 70000}}  # nothing else from research is pinned
    assert s["enabled"] is True and eng.config(s) == changed


def test_anchor_flags_are_always_stored_for_raw_sql_readers():
    full = eng.config(None)
    s = eng.overrides(full)
    for flag in ("enabled", "training_job_enabled"):          # selected via config_json->>'flag'
        assert flag in s and s[flag] == full[flag]


def test_listing_refresh_no_longer_freezes_defaults(monkeypatch):
    stored_box = {"json": {"enabled": True, "training_job_enabled": True}}

    async def fake_get(db, user_id):
        return eng.config(stored_box["json"])

    class CS:
        async def update_config(self, db, ctype, user_id, payload, **kw):
            stored_box["json"] = payload

    monkeypatch.setattr(svc, "get_config", fake_get)
    import app.services.config_service as cs_mod
    monkeypatch.setattr(cs_mod, "config_service", CS())
    asyncio.run(svc.put_config(None, "u", {"listing_records": {}, "listing_ids": {}}))
    assert "research" not in stored_box["json"]               # the job writes no research defaults
    assert stored_box["json"]["enabled"] is True and stored_box["json"]["training_job_enabled"] is True


def _migration():
    fake = types.ModuleType("alembic"); fake.op = None
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake
    try:
        path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "240_pump_unfreeze_candle.py"
        spec = importlib.util.spec_from_file_location("m240", path)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            sys.modules.pop("alembic", None)


def test_migration_removes_only_values_still_at_the_old_defaults():
    m = _migration()
    frozen = {"enabled": True, "research": {"candle": {"max_rows": 40000, "max_rows_per_time": 5,
                                                       "horizons_minutes": [10, 15], "lookback_days": 90,
                                                       "compare_label_modes": ["endpoint", "path_mean"]}}}
    new, removed = m.unfreeze(frozen)
    assert sorted(removed) == ["compare_label_modes", "horizons_minutes", "max_rows", "max_rows_per_time"]
    assert new["research"]["candle"] == {"lookback_days": 90} and frozen["research"]["candle"]["max_rows"] == 40000
    applied = eng.config(new)["research"]["candle"]
    assert applied["max_rows"] == 100000 and applied["horizons_minutes"] == [15, 10]
    chosen = deepcopy(frozen); chosen["research"]["candle"]["max_rows"] = 60000      # a real choice survives
    new2, removed2 = m.unfreeze(chosen)
    assert new2["research"]["candle"]["max_rows"] == 60000 and "max_rows" not in removed2
    assert m.unfreeze({"enabled": True}) == ({"enabled": True}, [])
    assert len(m.revision) <= 32
