"""Daily Pump directional challenger, run from the Celery worker (2026-10-07).

Port of ``pump_ml/job.py`` (the Railway upload-source service, which a merge never
redeploys). Same ledger, advisory lock and one-run-per-UTC-day rule, so whichever
runner starts first owns the day and the other records ``daily_already_recorded``.

Fixes versus the Railway job, all observed in production ``pump_ml_job_runs``:
  * 06/10 ``CheckViolationError``: directional artifacts used ``pump_directional/``
    while both tables only accept ``pump_ml/%``. Namespace is now ``pump_ml/directional/``.
  * 03/10 and 07/10 ``QueryCanceledError``: candidate cursors ordered by
    ``decision_at`` without an index (migration 237 adds it) under a 30 s
    statement timeout; the read timeout is now configurable (default 120 s).
  * Fits run in-process with one thread (no ``pump_ml`` package in the image).

Objective stays the endpoint DIRECTION at each horizon (no fixed % target).
Promotion is NOT decided here: models are stored as ``challenger`` and the Pump
Score v1 applies one only when its stored test metrics pass ``score_v1.ml.quality``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .pump_contracts import CONTEXT_FEATURE_SPEC, FEATURE_SPEC
from .pump_opportunity_engine import canonical_hash, config, utc

logger = logging.getLogger(__name__)

MAX_RUNTIME_SECONDS = 840
MAX_ROWS = 10000
STORAGE_SQL = """SELECT sum(pg_total_relation_size(name::regclass)) FROM unnest(ARRAY[
    'pump_opportunity_observations','pump_opportunity_labels','pump_opportunity_price_paths',
    'pump_opportunity_label_queue','pump_opportunity_job_runs','pump_ml_experiments','pump_ml_predictions',
    'pump_ml_job_runs','pump_ml_artifacts']) name"""


def prepare(rows, selection=None, *, features, cuts=(.5, .7, .85)):
    """Mechanical challenger floors, never a model-validation/promotion gate."""
    if len(rows) < 200:
        raise ValueError("insufficient_compatible_rows_min200_challenger_only")
    rows = sorted(rows, key=lambda r: (utc(r["decision_at"]), r["observation_id"]))
    times = sorted({utc(r["decision_at"]) for r in rows})
    if len(times) < 8:
        raise ValueError("insufficient_distinct_temporal_captures")
    cuts = [times[int(len(times) * f)].isoformat() for f in cuts]
    first = rows[0]["manifest"]
    return {"features": features, "feature_spec_hash": canonical_hash(FEATURE_SPEC),
            "source_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("SOURCE_COMMIT", "local_test"),
            "selection_policy": selection,
            "producer_config_hash": first["legacy_config_hash"], "label_spec": rows[0]["label_spec"],
            "label_spec_hash": first["label_spec_hash"],
            "reference_policy": "gate_best_ask_v1", "cost_policy": rows[0]["label_spec"]["cost_policy"],
            "cost_policy_hash": first["cost_policy_hash"],
            "boundaries": cuts, "embargo_seconds": 7200, "min_episodes": 100, "min_days": 1, "min_instruments": 10,
            "max_rows": MAX_ROWS, "max_threads": 1,
            "params": {"n_estimators": 100, "max_depth": 3, "random_state": 20261001},
            "decision_threshold": .5,
            "support_criteria": {"status": "PROVISIONAL_CHALLENGER_ONLY",
                                 "selection_floor": "mechanical_not_statistically_validated",
                                 "validation_required": ["out_of_time_test_quality_gate_at_inference"],
                                 "auto_promotion": False}}


def prepare_directional(rows, horizon, research, selection):
    from .pump_directional_research import target_contract
    if len(rows) < research["min_rows_per_horizon"]:
        raise ValueError("insufficient_directional_rows")
    spec = prepare(rows, selection, features=research["features"], cuts=tuple(research["cohort_cuts"]))
    context = list(research.get("context_features") or [])
    spec.update(features=research["features"], max_rows=research["max_rows"], min_episodes=research["min_episodes"],
                context_features=context,
                walk_forward=research.get("walk_forward"),
                # Parameters of the derived relative features, frozen into the manifest so
                # live inference recomputes them identically.
                relative_beta=(research.get("relative_beta") if (research.get("relative_beta") or {}).get("enabled")
                               and research.get("target_mode") == "relative_universe_median" else None),
                context_feature_spec_hash=canonical_hash(CONTEXT_FEATURE_SPEC) if context else None,
                cohort_cuts=list(research["cohort_cuts"]),
                min_days=research["min_days"], min_instruments=research["min_instruments"], params=research["params"],
                directional_target=target_contract(horizon, research.get("target_mode") == "relative_universe_median",
                                                   int(research.get("relative_min_assets") or 10),
                                                   bool((research.get("relative_beta") or {}).get("enabled"))),
                directional_evaluation={k: research[k] for k in ("bootstrap_repetitions", "reliability_bins",
                                                                  "calibration_C", "calibration_max_iter",
                                                                  "calibration_method", "calibration_pool",
                                                                  "calibration_max_slope")})
    return spec


def _fit(rows, spec, output_root):
    from .pump_directional_research import train_directional
    try:
        return train_directional(rows, spec=spec, output_root=output_root)
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc), "support_diagnostics": getattr(exc, "details", None)}


async def run_directional_horizons(conn, owner, c, diagnostics, staging, start, horizons):
    from .pump_ml_selection import select_temporal_training_rows
    research = c["research"]
    results = []
    quota = research["max_rows"] // len(horizons)
    diagnostics.update(objective=research["objective"], horizons={}, shared_max_rows=research["max_rows"])
    for h in horizons:
        remaining = MAX_RUNTIME_SECONDS - (datetime.now(timezone.utc) - start).total_seconds()
        if remaining <= 0:
            raise ValueError("directional_shared_runtime_budget_exhausted")
        diagnostic: Dict[str, Any] = {}
        diagnostics["horizons"][str(h)] = diagnostic
        contract, rows = await select_temporal_training_rows(
            conn, owner, research["features"], canonical_hash(FEATURE_SPEC), quota, diagnostic,
            bins=research["temporal_bins"], horizon_minutes=h,
            extra_features=list(research.get("context_features") or []),
            lookback_days=int(research.get("lookback_days") or 30),
            benchmark=research.get("target_mode") == "relative_universe_median",
            beta=research.get("relative_beta") if (research.get("relative_beta") or {}).get("enabled") else None,
            max_rows_per_minute=int(research.get("max_rows_per_minute") or 0))
        if not contract:
            raise ValueError("no_certified_point_in_time_listing_cohort")
        try:
            spec = prepare_directional(rows, h, research, diagnostic.get("policy"))
        except ValueError as exc:
            diagnostic["blocked_reason"] = str(exc)
            continue
        remaining = MAX_RUNTIME_SECONDS - (datetime.now(timezone.utc) - start).total_seconds()
        result = await asyncio.wait_for(asyncio.to_thread(_fit, rows, spec, staging), timeout=max(1, remaining))
        if result.get("status") == "blocked":
            diagnostic["blocked_reason"] = result["reason"]
            diagnostic["support_diagnostics"] = result.get("support_diagnostics")
            continue
        results.append(result)
    return results


FAMILIES = ("observation", "candle", "candle_ablation")


class _noop:
    """Async no-op context (ablation saves nothing: no transaction is opened)."""
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False
# Ledger rows without ``family`` predate 2026-10-07 and belong to the observation family.
_FAMILY_SQL = "AND coalesce(payload->>'family','observation')=$3"


def _lock_key(owner, family: str) -> str:
    return f"pump_ml:{owner}" if family == "observation" else f"pump_ml:{family}:{owner}"


async def run_owner(conn, owner, *, horizons: List[int], force: bool = False,
                    min_interval_minutes: int = 5, family: str = "observation") -> Dict[str, Any]:
    """``force`` (manual trigger) skips the one-run-per-UTC-day rule but never the
    singleton lock, and refuses while a run is in progress or one started less
    than ``min_interval_minutes`` ago (``score_v1.ml.training.manual_min_interval_minutes``).
    Each ``family`` (observation | candle) has its own lock, daily rule and budget."""
    if family not in FAMILIES:
        raise ValueError(f"unknown_family:{family}")
    lock = _lock_key(owner, family)
    if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", lock):
        return {"owner": str(owner), "status": "singleton_busy", "family": family}
    if force:
        prior = await conn.fetchval(
            "SELECT run_id FROM pump_ml_job_runs WHERE user_id=$1 AND (status='running' AND deadline_at>now() "
            f"OR started_at>=now()-make_interval(mins=>$2)) {_FAMILY_SQL} LIMIT 1", owner, int(min_interval_minutes),
            family)
        skip_status = "recent_run_exists"
    else:
        prior = await conn.fetchval(
            "SELECT run_id FROM pump_ml_job_runs WHERE user_id=$1 AND "
            "started_at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
            "AND coalesce(payload->>'family','observation')=$2 LIMIT 1", owner, family)
        skip_status = "daily_already_recorded"
    if prior:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", lock)
        return {"owner": str(owner), "status": skip_status, "run_id": str(prior)}
    run_id = uuid4()
    start = datetime.now(timezone.utc)
    selection: Dict[str, Any] = {}
    await conn.execute(
        "INSERT INTO pump_ml_job_runs(run_id,user_id,deadline_at,status,payload) VALUES($1,$2,$3,'running',$4)",
        run_id, owner, start + timedelta(seconds=MAX_RUNTIME_SECONDS + 60),
        {"runner": "celery", "trigger": "manual" if force else "schedule", "threads": 1, "family": family,
         "max_runtime_seconds": MAX_RUNTIME_SECONDS, "applied_delta": 0})
    try:
        raw = await conn.fetchval(
            "SELECT config_json FROM config_profiles WHERE user_id=$1 AND config_type='pump_opportunity' "
            "AND pool_id IS NULL AND is_active IS NOT FALSE ORDER BY updated_at DESC LIMIT 1", owner)
        c = config(raw)
        if not c["enabled"] or not c.get("training_job_enabled"):
            raise ValueError("training_job_disabled")
        configured = [h for h in horizons if h in c["labels"]["horizons_minutes"]]
        if not configured:
            raise ValueError("no_configured_training_horizon")
        used = await conn.fetchval(STORAGE_SQL)
        if (used or 0) + 5_000_000 > c["budget"]["max_storage_bytes"]:
            raise ValueError("pump_storage_budget_exhausted")
        ablation = None
        with tempfile.TemporaryDirectory(prefix="pump_ml_") as staging:
            if family == "candle_ablation":
                from .pump_ml_candles import run_candle_ablation
                ablation = await run_candle_ablation(conn, owner, c, selection,
                                                     start + timedelta(seconds=MAX_RUNTIME_SECONDS))
                results = []
            elif family == "candle":
                from .pump_ml_candles import run_candle_horizons
                if not c["research"]["candle"]["enabled"]:
                    raise ValueError("candle_family_disabled")
                results = await run_candle_horizons(conn, owner, c, selection, staging,
                                                    start + timedelta(seconds=MAX_RUNTIME_SECONDS))
            else:
                results = await run_directional_horizons(conn, owner, c, selection, staging, start, configured)
            artifacts = []
            for result in results:
                for p in (Path(staging) / result["manifest"]["artifact_namespace"]).iterdir():
                    artifacts.append((result, str(p.name), p.read_bytes()))
            artifact_bytes = sum(len(content) for _, _, content in artifacts)
            if artifact_bytes > 5_000_000:
                raise ValueError("artifact_budget_exceeded")
            async with (conn.transaction() if results else _noop()):
                for result in results:
                    manifest = result["manifest"]
                    experiment = uuid5(NAMESPACE_URL, f"pump_registry:{owner}:{manifest['experiment_id']}")
                    await conn.execute(
                        "INSERT INTO pump_ml_experiments(experiment_id,user_id,manifest,metrics,artifact_namespace,status) "
                        "VALUES($1,$2,$3,$4,$5,'challenger') ON CONFLICT DO NOTHING",
                        experiment, owner, manifest, result["metrics"], manifest["artifact_namespace"])
                    for artifact_result, name, content in artifacts:
                        if artifact_result is result:
                            await conn.execute(
                                "INSERT INTO pump_ml_artifacts(experiment_id,path,sha256,content) "
                                "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                                experiment, f"{manifest['artifact_namespace']}/{name}",
                                hashlib.sha256(content).hexdigest(), content)
        outcome = {"status": "challenger" if results else "blocked",
                   "objective": ("relative_candle_v1" if family == "candle" else
                                 "relative_direction_v1" if (c.get("research") or {}).get("target_mode") == "relative_universe_median"
                                 else "endpoint_direction_v1"),
                   "reason": None if results else "no_horizon_with_sufficient_directional_support",
                   "experiments": [{"experiment_id": str(uuid5(NAMESPACE_URL,
                                                               f"pump_registry:{owner}:{r['manifest']['experiment_id']}")),
                                    "horizon_minutes": r["manifest"]["spec"]["directional_target"]["horizon_minutes"],
                                    "cohort_rows": r["metrics"]["cohort_rows"]} for r in results],
                   "artifacts": len(artifacts), "artifact_bytes": artifact_bytes,
                   "applied_delta": 0, "auto_promotion": False}
        if ablation is not None:   # research only: nothing saved, nothing applied
            outcome = {"status": "ablation", "ablation": ablation, "applied_delta": 0, "auto_promotion": False}
    except ValueError as exc:
        outcome = {"status": "blocked", "reason": str(exc), "applied_delta": 0, "production_model_validated": False}
    except Exception as exc:
        logger.exception("[PUMP-ML] daily run failed owner=%s", owner)
        outcome = {"status": "failed", "reason": type(exc).__name__, "detail": str(exc)[:300], "applied_delta": 0}
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", lock)
    outcome.update(run_id=str(run_id), owner=str(owner), runner="celery", trigger="manual" if force else "schedule",
                   family=family,
                   source_commit=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("SOURCE_COMMIT", "local_test"),
                   duration_seconds=round((datetime.now(timezone.utc) - start).total_seconds(), 3),
                   selection=selection)
    await conn.execute("UPDATE pump_ml_job_runs SET finished_at=now(),status=$2,payload=$3 WHERE run_id=$1",
                       run_id, outcome["status"], outcome)
    return outcome


async def _owners(conn) -> List[UUID]:
    rows = await conn.fetch(
        "SELECT DISTINCT user_id FROM config_profiles WHERE config_type='pump_opportunity' "
        "AND pool_id IS NULL AND is_active IS NOT FALSE AND (config_json->>'training_job_enabled')::boolean IS TRUE")
    return [r["user_id"] for r in rows]


async def _training_spec(conn, owner) -> Dict[str, Any]:
    """``score_v1.ml.training`` from the owner's Pump Monitor config (seed when absent)."""
    from . import pump_monitor_engine as eng
    stored = await conn.fetchval(
        "SELECT config_json FROM config_profiles WHERE user_id=$1 AND config_type='pump_monitor' "
        "AND is_active IS NOT FALSE ORDER BY updated_at DESC LIMIT 1", owner)
    return eng.effective_config(stored)["score_v1"]["ml"]["training"]


async def run_daily(*, owner: str = None, force: bool = False, family: str = "observation") -> Dict[str, Any]:
    import asyncpg
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    out: Dict[str, Any] = {}
    conn = await asyncpg.connect(url, timeout=10, server_settings={"application_name": "pump_ml_daily_celery"})
    for kind in ("json", "jsonb"):
        await conn.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        owners = await _owners(conn)
        if owner is not None:
            owners = [o for o in owners if str(o) == str(owner)]
            if not owners:
                return {str(owner): {"status": "skipped", "reason": "training_job_disabled_or_unknown_owner"}}
        for owner_id in owners:
            training = await _training_spec(conn, owner_id)
            if not training.get("enabled", True):
                out[str(owner_id)] = {"status": "skipped", "reason": "score_v1.ml.training.enabled=false"}
                continue
            await conn.execute(f"SET statement_timeout = {int(training['statement_timeout_ms'])}")
            out[str(owner_id)] = await run_owner(conn, owner_id, horizons=[int(h) for h in training["horizons_minutes"]],
                                                 force=force, family=family,
                                                 min_interval_minutes=int(training.get("manual_min_interval_minutes", 5)))
        return out
    finally:
        await conn.close()
