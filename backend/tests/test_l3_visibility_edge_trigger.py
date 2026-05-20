"""
Tests for the L3 visibility edge-triggered guarantee in ``pipeline_scan``.

Rule (see ``_prior_l3_visibility`` / ``_save_l3_visibility`` and the decision
loop in ``run_pipeline_scan``):

    not in L3 → in L3     ⇒ force-log once (event_type = 'L3_VISIBLE')
    in L3 → in L3         ⇒ skip (no heartbeat)
    in L3 → not in L3     ⇒ remove from set (reset)
    not in L3 → not L3    ⇒ noop

Symbol re-entering L3 after exiting must produce a NEW row.
"""

from __future__ import annotations

import pytest

from app.tasks.pipeline_scan import (
    _prior_l3_visibility,
    _save_l3_visibility,
    _should_log_decision,
)


# ─── Fake Redis (minimal — only the surface used by the helpers) ──────────────


class _FakePipeline:
    def __init__(self, store: dict):
        self._store = store
        self._ops: list = []

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    def sadd(self, key, *members):
        self._ops.append(("sadd", key, members))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "delete":
                self._store.pop(op[1], None)
            elif op[0] == "sadd":
                _, key, members = op
                bucket = self._store.setdefault(key, set())
                bucket.update(members)
            elif op[0] == "expire":
                # no-op for the fake — TTL is not exercised here
                pass
        self._ops.clear()


class FakeRedis:
    def __init__(self):
        self._store: dict = {}

    def smembers(self, key):
        return set(self._store.get(key, set()))

    def pipeline(self):
        return _FakePipeline(self._store)


# ─── Roundtrip ────────────────────────────────────────────────────────────────


def test_visibility_roundtrip_empty():
    r = FakeRedis()
    assert _prior_l3_visibility(r, "wl-1") == set()


def test_visibility_save_then_load():
    r = FakeRedis()
    _save_l3_visibility(r, "wl-1", {"LINK_USDT", "ADA_USDT"})
    assert _prior_l3_visibility(r, "wl-1") == {"LINK_USDT", "ADA_USDT"}


def test_visibility_save_replaces_set_not_appends():
    r = FakeRedis()
    _save_l3_visibility(r, "wl-1", {"LINK_USDT", "ADA_USDT"})
    # Second save with a different set must REPLACE (symbol that left L3 is gone)
    _save_l3_visibility(r, "wl-1", {"LINK_USDT"})
    assert _prior_l3_visibility(r, "wl-1") == {"LINK_USDT"}


def test_visibility_empty_save_clears_set():
    r = FakeRedis()
    _save_l3_visibility(r, "wl-1", {"LINK_USDT"})
    _save_l3_visibility(r, "wl-1", set())
    assert _prior_l3_visibility(r, "wl-1") == set()


def test_visibility_isolated_per_watchlist():
    r = FakeRedis()
    _save_l3_visibility(r, "wl-1", {"LINK_USDT"})
    _save_l3_visibility(r, "wl-2", {"ADA_USDT"})
    assert _prior_l3_visibility(r, "wl-1") == {"LINK_USDT"}
    assert _prior_l3_visibility(r, "wl-2") == {"ADA_USDT"}


def test_visibility_no_redis_returns_empty_and_no_raise():
    assert _prior_l3_visibility(None, "wl-1") == set()
    _save_l3_visibility(None, "wl-1", {"LINK_USDT"})  # must not raise


# ─── Edge-trigger rule (logic that lives inline in run_pipeline_scan) ─────────


def _simulate_decision_loop(decisions, prior_states, prior_visibility):
    """
    Mirror the edge-trigger fragment of ``run_pipeline_scan`` so the rule can
    be tested in isolation.  Must match the production loop body exactly.
    """
    new_states: dict = {}
    current_visibility: set = set()
    decisions_to_log: list = []

    for d in sorted(decisions, key=lambda x: x.get("symbol") or ""):
        sym = d.get("symbol")
        prior = prior_states.get(sym)
        should_log, event_type = _should_log_decision(d, prior)

        if d.get("decision") == "ALLOW":
            current_visibility.add(sym)
            if not should_log and sym not in prior_visibility:
                should_log = True
                event_type = "L3_VISIBLE"

        new_states[sym] = {
            "state": d.get("decision"),
            "score": d.get("score"),
            "direction": d.get("direction"),
            "db_confirmed_at": (prior or {}).get("db_confirmed_at"),
        }
        if should_log:
            d["event_type"] = event_type
            decisions_to_log.append(d)

    return decisions_to_log, current_visibility, new_states


def _allow(sym, score=60.0):
    return {"symbol": sym, "decision": "ALLOW", "score": score, "strategy": "L3"}


def _block(sym, score=20.0):
    return {"symbol": sym, "decision": "BLOCK", "score": score, "strategy": "L3"}


def test_stable_allow_with_no_prior_visibility_emits_l3_visible():
    """
    The user's reported scenario: LINK and ADA are stably ALLOW in L3 but never
    appear in the Decision Log because ``_should_log_decision`` skips
    ALLOW→ALLOW with small score delta.  The edge-trigger must force one row.
    """
    decisions = [_allow("LINK_USDT", 63), _allow("ADA_USDT", 58)]
    # Both symbols already had an ALLOW state in Redis (stable cycle),
    # so the transition detector returns (False, None) for each.
    prior_states = {
        "LINK_USDT": {"state": "ALLOW", "score": 62.5, "db_confirmed_at": "x"},
        "ADA_USDT":  {"state": "ALLOW", "score": 57.8, "db_confirmed_at": "x"},
    }
    prior_visibility: set = set()  # cold visibility cache

    to_log, current_vis, _ = _simulate_decision_loop(decisions, prior_states, prior_visibility)

    symbols_logged = {d["symbol"]: d["event_type"] for d in to_log}
    assert symbols_logged == {
        "LINK_USDT": "L3_VISIBLE",
        "ADA_USDT":  "L3_VISIBLE",
    }
    assert current_vis == {"LINK_USDT", "ADA_USDT"}


def test_stable_allow_with_prior_visibility_does_not_re_emit():
    """In-cycle stable ALLOW must NOT generate heartbeats."""
    decisions = [_allow("LINK_USDT", 63)]
    prior_states = {"LINK_USDT": {"state": "ALLOW", "score": 62.5, "db_confirmed_at": "x"}}
    prior_visibility = {"LINK_USDT"}

    to_log, current_vis, _ = _simulate_decision_loop(decisions, prior_states, prior_visibility)

    assert to_log == []  # no re-emit
    assert current_vis == {"LINK_USDT"}  # still tracked as visible


def test_transition_takes_precedence_over_l3_visible():
    """
    NEW_SIGNAL / SIGNAL_REGAINED / etc. always win — L3_VISIBLE only fires
    when the transition detector would otherwise skip the row.
    """
    decisions = [_allow("NEW_USDT", 70)]
    prior_states: dict = {}  # never seen → NEW_SIGNAL
    prior_visibility: set = set()

    to_log, _, _ = _simulate_decision_loop(decisions, prior_states, prior_visibility)

    assert len(to_log) == 1
    assert to_log[0]["event_type"] == "NEW_SIGNAL"  # not overridden


def test_symbol_leaving_l3_is_dropped_from_current_visibility():
    """
    Symbol present in prior_visibility but NOT in current ALLOW decisions must
    not appear in current_visibility — the save call will purge it from Redis.
    """
    decisions = [_allow("LINK_USDT"), _block("ADA_USDT")]
    prior_states = {
        "LINK_USDT": {"state": "ALLOW", "score": 60.0, "db_confirmed_at": "x"},
        "ADA_USDT":  {"state": "ALLOW", "score": 60.0, "db_confirmed_at": "x"},
    }
    prior_visibility = {"LINK_USDT", "ADA_USDT"}

    to_log, current_vis, _ = _simulate_decision_loop(decisions, prior_states, prior_visibility)

    # ADA_USDT is gone from current_vis → next _save_l3_visibility wipes it
    assert current_vis == {"LINK_USDT"}
    # ADA_USDT exits ALLOW → SIGNAL_LOST (transition detector handles this row)
    assert {d["symbol"] for d in to_log} == {"ADA_USDT"}
    assert next(d["event_type"] for d in to_log if d["symbol"] == "ADA_USDT") == "SIGNAL_LOST"


def test_symbol_re_enters_l3_after_exit_emits_again():
    """
    Full presence-cycle reset: a symbol that left L3 (was removed from the
    visibility set on the previous scan) and now re-enters must produce a row.
    """
    # Scan N-1 result: ADA exited, _save_l3_visibility called with only LINK.
    # Scan N: ADA re-enters as stable ALLOW (prior_state ALLOW because Redis
    # decision_states still has it within its own TTL window — but visibility
    # set was already purged).
    decisions = [_allow("ADA_USDT", 58)]
    prior_states = {"ADA_USDT": {"state": "ALLOW", "score": 57.5, "db_confirmed_at": "x"}}
    prior_visibility: set = {"LINK_USDT"}  # ADA explicitly absent

    to_log, current_vis, _ = _simulate_decision_loop(decisions, prior_states, prior_visibility)

    assert len(to_log) == 1
    assert to_log[0]["event_type"] == "L3_VISIBLE"
    assert current_vis == {"ADA_USDT"}


def test_only_allow_symbols_added_to_visibility():
    """BLOCK symbols are never tracked in the visibility set."""
    decisions = [_allow("LINK_USDT"), _block("FOO_USDT"), _block("BAR_USDT")]
    prior_states: dict = {}
    prior_visibility: set = set()

    _, current_vis, _ = _simulate_decision_loop(decisions, prior_states, prior_visibility)

    assert current_vis == {"LINK_USDT"}


# ─── Markers required by deadlock-sort invariants (Task #310) ─────────────────


def test_decision_loop_iterates_in_sorted_order():
    """
    The production loop uses ``sorted(decisions, key=lambda x: x.get("symbol"))``
    to preserve deterministic row-lock acquisition order in decisions_log
    inserts.  This test pins the contract.
    """
    decisions = [_allow("ZZZ_USDT"), _allow("AAA_USDT"), _allow("MMM_USDT")]
    to_log, _, _ = _simulate_decision_loop(decisions, {}, set())
    # NEW_SIGNAL for all three, but the *order* must be alphabetical because
    # _persist_decision_logs iterates the list when issuing INSERTs.
    assert [d["symbol"] for d in to_log] == ["AAA_USDT", "MMM_USDT", "ZZZ_USDT"]
