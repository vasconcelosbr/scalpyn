"""Real per-metric aggregate handlers for the shadow_portfolio AI tool catalog.

Before this module, every tool declared in module_registry.py's
shadow_portfolio capability list (get_performance_summary, get_mae_mfe,
get_score_buckets, get_delayed_tp, get_outcome_horizons, get_data_quality,
get_experiment_status, get_experiment_result, compare_champion_candidate,
get_profile_performance, freeze_analysis_dataset) was registered with the
same generic row-dump handler (_bounded_frozen_reader in
module_tool_runtime.py) -- 11 differently-named tools returning byte-
identical output, confirmed 2026-08-17 against a live root-cause-audit
request (10 of 11 outputs shared the exact same data hash).

Each function below computes its own real aggregate from the complete
canonical items persisted for one report-backed Intelligence Run. Pure
functions keep the calculations deterministic after the runtime has verified
tenant scope, item count, contract version, and dataset hash.

Aggregation logic mirrors, at reduced precision, the equivalent dashboard
endpoints in api/shadow_trades.py (shadow_trades_summary, shadow_trades_
analytics, shadow_trades_timeout_analysis) so the AI's numbers agree with
what an operator sees on screen for the same population, rather than
inventing a parallel definition of win rate / MAE / MFE / recovery.

freeze_analysis_dataset returns only the verified coverage manifest. Raw
trades reach the provider exclusively through the hashed canonical shards;
the tool layer never creates a second summarized dataset.
"""

from __future__ import annotations

import math
from typing import Any

from .tool_registry import ToolCapability


def _pct_change(after: Any, entry: Any) -> float | None:
    try:
        after_value = float(after)
        entry_value = float(entry)
    except (TypeError, ValueError):
        return None
    if entry_value == 0:
        return None
    return (after_value - entry_value) / entry_value * 100


def canonical_analytics_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive compact calculation views from complete persisted canonical items.

    No feature catalog is consulted: every finite numeric value in both full
    feature snapshots remains discoverable through ``indicators_by_phase``.
    """
    output: list[dict[str, Any]] = []
    for canonical in rows:
        trade = dict(canonical.get("trade") or {})
        snapshots = canonical.get("snapshots") or {}
        entry_features = dict(snapshots.get("entry_features") or {})
        exit_features = dict(snapshots.get("exit_features") or {})
        configuration = snapshots.get("configuration") or {}
        risk = snapshots.get("entry_risk") or {}
        output.append({
            **trade,
            "id": str(trade.get("id") or ""),
            "event_identity": str(trade.get("event_id") or trade.get("id") or ""),
            "lineage_status": trade.get("lineage_status"),
            "final_score": configuration.get("final_score"),
            "horizon_change_pct": {
                horizon: _pct_change(trade.get(f"price_after_{horizon}"), trade.get("entry_price"))
                for horizon in ("1h", "2h", "4h", "12h", "24h")
            },
            "entry_risk_contract_valid": (
                (risk.get("contract_status") or {}).get("entry_risk_contract_valid")
            ),
            "indicators_by_phase": {
                "entry": entry_features,
                "exit": exit_features,
            },
            "canonical_item_hash": None,
        })
    return output


def _envelope(
    capability: ToolCapability,
    *,
    data: Any,
    evidence_ids: list[str],
    dataset_hash: str,
    dataset_window_end: str,
    quality: str,
    missingness: list[str],
) -> dict[str, Any]:
    return {
        "tool": capability.name,
        "contract_version": capability.version,
        "data": data,
        "evidence_ids": evidence_ids,
        "freshness": {
            "dataset_window_end": dataset_window_end,
            "dataset_hash": dataset_hash,
            "sla_seconds": capability.freshness_sla_seconds,
        },
        "quality": quality,
        "missingness": missingness,
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _rate_pct(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _pnl_values(group: list[dict]) -> list[float]:
    return [r["pnl_pct"] for r in group if r.get("pnl_pct") is not None]


def _quality_for(rows: list[dict], required_keys: tuple[str, ...]) -> tuple[str, list[str]]:
    if not rows:
        return "NO_DATA", []
    missingness = sorted({
        key for row in rows for key in required_keys
        if row.get(key) is None
    })
    return ("PASS_WITH_MISSINGNESS" if missingness else "PASS"), missingness


def _evidence_ids(rows: list[dict]) -> list[str]:
    return [r["id"] for r in rows if r.get("id")]


def performance_summary(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Real analogue of GET /shadow-trades/summary, scoped to this dataset."""
    completed = [r for r in rows if r.get("status") == "COMPLETED"]
    win = [r for r in completed if r.get("outcome") == "TP_HIT"]
    loss = [r for r in completed if r.get("outcome") == "SL_HIT"]
    timeout = [r for r in completed if r.get("outcome") == "TIMEOUT"]
    pnl_usdt_values = [r["pnl_usdt"] for r in rows if r.get("pnl_usdt") is not None]

    data = {
        "total": len(rows),
        "pending": len(rows) - len(completed),
        "completed": len(completed),
        "win": len(win), "loss": len(loss), "timeout": len(timeout),
        "win_rate_pct": _rate_pct(len(win), len(completed)),
        "avg_pnl_pct": _mean(_pnl_values(completed)),
        "total_pnl_usdt": round(sum(pnl_usdt_values), 4) if pnl_usdt_values else None,
    }
    quality, missingness = _quality_for(completed, ("pnl_pct", "pnl_usdt"))
    if not rows:
        quality = "NO_DATA"
    return _envelope(
        capability, data=data, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missingness,
    )


def _profile_groups(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = row.get("profile_id") or "UNKNOWN"
        groups.setdefault(key, []).append(row)
    return groups


def _profile_bucket(profile_id: str, group: list[dict]) -> dict[str, Any]:
    completed = [r for r in group if r.get("status") == "COMPLETED"]
    win = [r for r in completed if r.get("outcome") == "TP_HIT"]
    return {
        "profile_id": profile_id,
        "sample_size": len(group),
        "completed": len(completed),
        "win_rate_pct": _rate_pct(len(win), len(completed)),
        "avg_pnl_pct": _mean(_pnl_values(completed)),
    }


def profile_performance(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Win rate / avg pnl grouped by profile_id, ranked -- best first.

    Deliberately simpler than the canonical weighted ev_score ranking in
    watchlist_performance_ranking_service.py (that service is async and
    queries its own config-driven weights; this handler is a synchronous
    pure function over the already-fetched dataset rows, matching every
    other shadow_portfolio tool's contract). It answers "how did each
    profile do in this dataset", not "how should profiles be ranked
    platform-wide" -- the two questions are related but not the same.
    """
    buckets = [_profile_bucket(pid, group) for pid, group in _profile_groups(rows).items()]
    buckets.sort(key=lambda b: (b["win_rate_pct"] is None, -(b["win_rate_pct"] or 0.0)))
    quality = "NO_DATA" if not rows else "PASS"
    return _envelope(
        capability, data={"profiles": buckets}, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=[],
    )


def score_buckets(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Outcome rate by final_score quartile (rank-based, not fixed 0-100
    bins) -- data-driven so it stays meaningful regardless of the score
    engine's current scale."""
    scored = sorted(
        (r for r in rows if r.get("final_score") is not None),
        key=lambda r: r["final_score"],
    )
    missing = [r["id"] for r in rows if r.get("final_score") is None and r.get("id")]
    n = len(scored)
    buckets: list[dict[str, Any]] = []
    if n:
        quartile_size = max(1, -(-n // 4))  # ceil(n / 4)
        for q in range(4):
            group = scored[q * quartile_size: (q + 1) * quartile_size]
            if not group:
                continue
            completed = [r for r in group if r.get("status") == "COMPLETED"]
            win = [r for r in completed if r.get("outcome") == "TP_HIT"]
            buckets.append({
                "quartile": q + 1,
                "score_min": group[0]["final_score"],
                "score_max": group[-1]["final_score"],
                "sample_size": len(group),
                "win_rate_pct": _rate_pct(len(win), len(completed)),
                "avg_pnl_pct": _mean(_pnl_values(completed)),
            })
    quality = "NO_DATA" if not rows else ("PASS_WITH_MISSINGNESS" if missing else "PASS")
    return _envelope(
        capability, data={"quartiles": buckets}, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=(["final_score"] if missing else []),
    )


def mae_mfe(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Real analogue of GET /shadow-trades/analytics's MAE/MFE section."""
    completed = [r for r in rows if r.get("status") == "COMPLETED"]
    tp = [r for r in completed if r.get("outcome") == "TP_HIT"]
    sl = [r for r in completed if r.get("outcome") == "SL_HIT"]

    def _group(group: list[dict]) -> dict[str, Any]:
        mae = [r["mae_pct"] for r in group if r.get("mae_pct") is not None]
        mfe = [r["mfe_pct"] for r in group if r.get("mfe_pct") is not None]
        return {"count": len(group), "avg_mae_pct": _mean(mae), "avg_mfe_pct": _mean(mfe)}

    near_sl_winners = [r for r in tp if (r.get("mae_pct") or 0) < -2.0]
    sl_after_mfe = [r for r in sl if (r.get("mfe_pct") or 0) > 1.0]
    recovery = [
        r["mfe_pct"] - r["mae_pct"] for r in tp
        if r.get("mae_pct") is not None and r.get("mfe_pct") is not None
    ]

    data = {
        "tp": _group(tp), "sl": _group(sl),
        "near_sl_winners_pct": _rate_pct(len(near_sl_winners), len(tp)),
        "sl_after_strong_mfe_pct": _rate_pct(len(sl_after_mfe), len(sl)),
        "avg_recovery_pct": _mean(recovery),
    }
    quality, missingness = _quality_for(completed, ("mae_pct", "mfe_pct"))
    if not rows:
        quality = "NO_DATA"
    return _envelope(
        capability, data=data, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missingness,
    )


def delayed_tp(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Real analogue of the timeout_post_analysis block in
    GET /shadow-trades/timeout-analysis, scoped to this dataset's TIMEOUT
    trades."""
    timeouts = [r for r in rows if r.get("outcome") == "TIMEOUT"]
    analyzed = [r for r in timeouts if r.get("timeout_post_analysis_done")]
    delayed = [r for r in analyzed if r.get("delayed_tp")]
    hours = [r["delayed_tp_hours"] for r in delayed if r.get("delayed_tp_hours") is not None]

    data = {
        "total_timeouts": len(timeouts),
        "analyzed": len(analyzed),
        "pending_analysis": len(timeouts) - len(analyzed),
        "delayed_tp_count": len(delayed),
        "timeout_recovery_rate_pct": _rate_pct(len(delayed), len(analyzed)),
        "avg_delayed_tp_hours": _mean(hours),
    }
    quality = "NO_DATA" if not timeouts else ("PASS_WITH_MISSINGNESS" if len(analyzed) < len(timeouts) else "PASS")
    missingness = ["timeout_post_analysis_done"] if len(analyzed) < len(timeouts) else []
    return _envelope(
        capability, data=data, evidence_ids=_evidence_ids(timeouts),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missingness,
    )


def outcome_horizons(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Avg post-exit price drift at fixed horizons (1h/2h/4h/12h/24h),
    real analogue of the avg_chg_1h..24h fields in
    GET /shadow-trades/timeout-analysis. Scoped to TIMEOUT trades, since
    that is the only outcome this observational tracking runs for
    (timeout_post_analysis, migration 063)."""
    timeouts = [r for r in rows if r.get("outcome") == "TIMEOUT"]
    horizons: dict[str, float | None] = {}
    missing_horizons: list[str] = []
    for horizon in ("1h", "2h", "4h", "12h", "24h"):
        values = [
            r["horizon_change_pct"][horizon] for r in timeouts
            if (r.get("horizon_change_pct") or {}).get(horizon) is not None
        ]
        horizons[horizon] = _mean(values)
        if len(values) < len(timeouts):
            missing_horizons.append(f"horizon_change_pct.{horizon}")
    quality = "NO_DATA" if not timeouts else ("PASS_WITH_MISSINGNESS" if missing_horizons else "PASS")
    return _envelope(
        capability, data={"avg_price_change_pct": horizons}, evidence_ids=_evidence_ids(timeouts),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missing_horizons,
    )


def data_quality(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Lineage/coverage health of the dataset itself -- distinct from every
    other handler here, which reports on trade *outcomes*. This one
    reports on whether the underlying capture can be trusted."""
    canonical = [r for r in rows if r.get("lineage_status") == "CANONICAL"]
    eligible = [r for r in rows if r.get("eligible_for_training")]
    coverage = [r["features_coverage"] for r in rows if r.get("features_coverage") is not None]
    confidence = [r["market_data_confidence"] for r in rows if r.get("market_data_confidence") is not None]
    age = [r["oldest_indicator_age_s"] for r in rows if r.get("oldest_indicator_age_s") is not None]

    data = {
        "total_rows": len(rows),
        "canonical_lineage_pct": _rate_pct(len(canonical), len(rows)),
        "eligible_for_training_pct": _rate_pct(len(eligible), len(rows)),
        "avg_features_coverage": _mean(coverage),
        "avg_market_data_confidence": _mean(confidence),
        "avg_oldest_indicator_age_s": _mean(age),
    }
    missingness = sorted({
        key for row in rows
        for key in ("features_coverage", "market_data_confidence", "oldest_indicator_age_s")
        if row.get(key) is None
    })
    quality = "NO_DATA" if not rows else ("PASS_WITH_MISSINGNESS" if missingness else "PASS")
    return _envelope(
        capability, data=data, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missingness,
    )


def compare_champion_candidate(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Ranks the distinct profiles present in this dataset by win rate and
    reports each one's delta against the top-ranked ("champion"). There is
    no separate champion/candidate designation upstream of this dataset --
    the comparison is defined empirically, over whatever profiles the
    caller scoped the request to (typically two, via entity_ids)."""
    buckets = [_profile_bucket(pid, group) for pid, group in _profile_groups(rows).items()]
    buckets.sort(key=lambda b: (b["win_rate_pct"] is None, -(b["win_rate_pct"] or 0.0)))
    champion = buckets[0] if buckets else None
    comparisons = []
    for bucket in buckets[1:]:
        delta_win_rate = (
            round(bucket["win_rate_pct"] - champion["win_rate_pct"], 2)
            if champion and bucket["win_rate_pct"] is not None and champion["win_rate_pct"] is not None
            else None
        )
        comparisons.append({**bucket, "role": "CANDIDATE", "win_rate_pct_vs_champion": delta_win_rate})
    data = {
        "champion": {**champion, "role": "CHAMPION"} if champion else None,
        "candidates": comparisons,
    }
    quality = "NO_DATA" if not buckets else ("PASS_WITH_MISSINGNESS" if len(buckets) < 2 else "PASS")
    missingness = [] if len(buckets) >= 2 else ["candidate_profile"]
    return _envelope(
        capability, data=data, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missingness,
    )


def indicator_lift(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Discover every numeric entry/exit indicator and calculate quartile lift."""
    completed = [r for r in rows if r.get("status") == "COMPLETED"]
    numeric: dict[tuple[str, str], list[tuple[float, dict[str, Any]]]] = {}
    all_names: dict[str, set[str]] = {"entry": set(), "exit": set()}
    for row in completed:
        for phase, features in (row.get("indicators_by_phase") or {}).items():
            if phase not in all_names or not isinstance(features, dict):
                continue
            all_names[phase].update(str(name) for name in features)
            for indicator, raw_value in features.items():
                if isinstance(raw_value, bool):
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    numeric.setdefault((phase, str(indicator)), []).append((value, row))

    indicators_out = []
    for (phase, indicator), observations in sorted(numeric.items()):
        observations.sort(key=lambda pair: pair[0])
        bucket_size = max(1, math.ceil(len(observations) / 4))
        for index in range(4):
            group = observations[index * bucket_size:(index + 1) * bucket_size]
            if not group:
                continue
            group_rows = [row for _, row in group]
            wins = [row for row in group_rows if row.get("outcome") == "TP_HIT"]
            indicators_out.append({
                "phase": phase,
                "indicator": indicator,
                "bucket": index + 1,
                "value_min": group[0][0],
                "value_max": group[-1][0],
                "sample_size": len(group_rows),
                "win_rate_pct": _rate_pct(len(wins), len(group_rows)),
                "avg_pnl_pct": _mean(_pnl_values(group_rows)),
            })

    missing_indicators = sorted(
        f"{phase}.{indicator}"
        for phase, names in all_names.items()
        for indicator in names
        if (phase, indicator) not in numeric
    )
    quality = "NO_DATA" if not completed else ("PASS_WITH_MISSINGNESS" if missing_indicators else "PASS")
    return _envelope(
        capability, data={"indicator_buckets": indicators_out}, evidence_ids=_evidence_ids(rows),
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality=quality, missingness=missing_indicators,
    )


def dataset_manifest(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """Verify the persisted complete dataset without echoing or truncating rows."""
    evidence_ids = [
        str((row.get("trade") or {}).get("id"))
        for row in rows
        if (row.get("trade") or {}).get("id")
    ]
    contracts = sorted({str(row.get("input_contract_version")) for row in rows})
    reports = sorted({str(row.get("report_run_id")) for row in rows})
    quality = "PASS" if rows and len(contracts) == 1 and len(reports) == 1 else "BLOCKED"
    return _envelope(
        capability,
        data={
            "verification_only": True,
            "item_count": len(rows),
            "input_contract_versions": contracts,
            "report_run_ids": reports,
            "dataset_hash": dataset_hash,
        },
        evidence_ids=evidence_ids,
        dataset_hash=dataset_hash,
        dataset_window_end=dataset_window_end,
        quality=quality,
        missingness=[] if quality == "PASS" else ["canonical_dataset_manifest"],
    )


def no_experiment_data(
    capability: ToolCapability, *, rows: list[dict], dataset_hash: str, dataset_window_end: str,
) -> dict[str, Any]:
    """get_experiment_status / get_experiment_result: shadow experiments
    (shadow.create_experiment, the regenerative-shadow-v1/v2 candidate ->
    shadow -> promote flow) are a real, distinct feature from the trade
    population these other handlers analyze -- but as of the audit that
    motivated this module (AUD-IR-ARCH-002, 2026-08-14), regeneration_runs
    has never had a row and no run has ever carried CANDIDATE_ONLY/
    SHADOW_ONLY authority in production. Reporting the trade dataset here
    (the old behaviour) would misrepresent an unrelated population as
    experiment evidence. Honest NO_DATA instead of a fabricated shape."""
    return _envelope(
        capability, data={"experiments_found": 0}, evidence_ids=[],
        dataset_hash=dataset_hash, dataset_window_end=dataset_window_end,
        quality="NO_DATA", missingness=["experiment_id"],
    )


SHADOW_PORTFOLIO_HANDLERS = {
    "shadow.freeze_analysis_dataset": dataset_manifest,
    "shadow.get_performance_summary": performance_summary,
    "shadow.get_profile_performance": profile_performance,
    "shadow.get_score_buckets": score_buckets,
    "shadow.get_mae_mfe": mae_mfe,
    "shadow.get_delayed_tp": delayed_tp,
    "shadow.get_outcome_horizons": outcome_horizons,
    "shadow.get_data_quality": data_quality,
    "shadow.get_indicator_lift": indicator_lift,
    "shadow.compare_champion_candidate": compare_champion_candidate,
    "shadow.get_experiment_status": no_experiment_data,
    "shadow.get_experiment_result": no_experiment_data,
}
