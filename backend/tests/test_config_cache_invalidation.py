"""Config cache invalidation after direct database writes (migrations).

Production finding 25/11/2026: migration 253 added ``pump_flow.revision`` to
config_profiles, but readers kept the cached JSON for up to the TTL; the MTF
settings screen showed the stale copy, and saving it would have removed the
migrated block.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.services import config_service as cs

ROOT = Path(__file__).resolve().parents[1]


class FakeRedis:
    def __init__(self, keys, fail=False):
        self.keys, self.fail, self.unlinked = set(keys), fail, []

    def scan_iter(self, match, count):
        if self.fail:
            raise ConnectionError("Timeout connecting to server")
        prefix = match.rstrip("*")
        return iter(sorted(k for k in self.keys if k.startswith(prefix)))

    def unlink(self, *keys):
        self.unlinked.append(keys)
        self.keys -= set(keys)
        return len(keys)


def test_invalidation_removes_only_config_keys_in_batches():
    service = cs.ConfigService()
    uid = uuid4()
    config_keys = {service._get_cache_key("mtf_analysis", uid), service._get_cache_key("pump_monitor", uid),
                   service._get_cache_key("score", uid, uuid4())}
    other = {"pump_monitor:latest:x", "trades_buffer:spot:BTC_USDT", "gate_ws:last_handover_ms"}
    fake = FakeRedis(config_keys | other)
    assert cs.invalidate_config_cache_sync(client=fake, batch=2) == 3
    assert fake.keys == other
    assert [len(b) for b in fake.unlinked] == [2, 1]


def test_invalidation_is_best_effort():
    assert cs.invalidate_config_cache_sync(client=FakeRedis({"config:a"}, fail=True)) is None
    assert cs.invalidate_config_cache_sync(redis_url="") is None
    assert cs.invalidate_config_cache_sync(redis_url="http://not-redis") is None


def test_cache_keys_share_the_invalidated_prefix():
    key = cs.ConfigService()._get_cache_key("mtf_analysis", uuid4())
    assert key.startswith(cs.CONFIG_CACHE_PREFIX) and key.endswith(":global:mtf_analysis")


def _env_module():
    return ast.parse((ROOT / "alembic" / "env.py").read_text(encoding="utf-8"))


def test_env_records_applied_migrations_but_not_stamps():
    tree = _env_module()
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_record_applied")
    namespace = {"_APPLIED": []}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "env.py", "exec"), namespace)
    record = namespace["_record_applied"]
    record(ctx=None, step=SimpleNamespace(is_stamp=False, is_upgrade=True, up_revision_id="253_x", down_revision_ids=()), heads=set(), run_args={})
    record(ctx=None, step=SimpleNamespace(is_stamp=True, is_upgrade=True, up_revision_id="253_x", down_revision_ids=()), heads=set(), run_args={})
    assert namespace["_APPLIED"] == ["253_x"]


def test_env_invalidates_after_commit_only_when_something_was_applied():
    source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "on_version_apply=_record_applied" in source
    run = source[source.index("async def run_async_migrations"):]
    dispose = run.index("await connectable.dispose()")
    invalidate = run.index("invalidate_config_cache_sync()")
    assert dispose < invalidate and "if _APPLIED:" in run[dispose:invalidate]
