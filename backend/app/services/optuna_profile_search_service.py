"""
Optuna Profile Search — optional hyperparameter search for profile configs.
Only runs when optuna is installed AND there's sufficient data.
"""
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False
    logger.info("[Optuna] optuna not available — search disabled")


def _is_available() -> bool:
    return _OPTUNA_AVAILABLE


def _optuna_validation_status(classification: dict) -> str:
    if classification["validation_status"] == "validated":
        return "optuna_validated"
    if classification["blocked_reason"] in {
        "blocked_low_discovery_support",
        "blocked_low_validation_support",
    }:
        return "optuna_blocked_low_support"
    if classification["blocked_reason"] == "blocked_no_validation":
        return "optuna_blocked_no_validation"
    return "optuna_blocked_overfit_risk"


class OptunaProfileSearchService:
    N_TRIALS = 200
    MIN_TRADES_FOR_SEARCH = 100

    async def search(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        lookback_days: int,
        base_metrics: dict,
        discovery_start: datetime,
        discovery_end: datetime,
        validation_start: datetime,
        validation_end: datetime,
        n_trials: int = 200,
    ) -> List[dict]:
        """
        Run Optuna search. Returns list of saved combinations.
        Skips gracefully if optuna not installed or data insufficient.
        """
        if not _OPTUNA_AVAILABLE:
            return []

        from .counterfactual_combination_service import (
            _load_trades_for_window,
            _match_trades,
            _window_metrics,
        )
        trades = await _load_trades_for_window(
            db, user_id, discovery_start, discovery_end
        )
        validation_trades = await _load_trades_for_window(
            db, user_id, validation_start, validation_end
        )

        if len(trades) < self.MIN_TRADES_FOR_SEARCH:
            logger.info("[Optuna] Insufficient data (%d trades) for user=%s", len(trades), user_id)
            return []

        base_win_rate = base_metrics.get("base_win_rate", 0.5)
        base_avg_pnl = base_metrics.get("base_avg_pnl_pct", 0)
        base_tp30m = base_metrics.get("base_tp_30m_rate", 0)

        def objective(trial):
            # Search space
            rsi_min = trial.suggest_float("rsi_min", 20, 55)
            rsi_max = trial.suggest_float("rsi_max", rsi_min + 5, 80)
            adx_min = trial.suggest_float("adx_min", 10, 35)
            adx_max = trial.suggest_float("adx_max", adx_min + 5, 50)
            zscore_min = trial.suggest_float("zscore_min", -3.5, 0)
            zscore_max = trial.suggest_float("zscore_max", 0.5, 3.0)
            vwap_max = trial.suggest_float("vwap_max", 0.5, 4.0)
            ema50_mode = trial.suggest_categorical("ema50_mode", ["true", "false", "ignore"])
            ema9_mode = trial.suggest_categorical("ema9_mode", ["true", "false", "ignore"])
            macd_required = trial.suggest_categorical("macd_required", ["true", "false"])
            vol_delta_min = trial.suggest_float("vol_delta_min", -30, 30)
            taker_min = trial.suggest_float("taker_min", 0.35, 0.70)
            obp_min = trial.suggest_float("obp_min", 0.20, 0.60)
            spread_max = trial.suggest_float("spread_max", 0.10, 0.35)

            # Apply filters
            matched = []
            for t in trades:
                f = t["features"]
                def get(k): return float(f.get(k) or 0) if f.get(k) is not None else None

                rsi = get("rsi")
                if rsi is None or not (rsi_min <= rsi <= rsi_max): continue

                adx = get("adx")
                if adx is None or not (adx_min <= adx <= adx_max): continue

                zsc = get("zscore")
                if zsc is None or not (zscore_min <= zsc <= zscore_max): continue

                vwap = get("vwap_distance_pct")
                if vwap is None or vwap > vwap_max: continue

                if ema50_mode != "ignore":
                    v = f.get("ema50_gt_ema200")
                    if v is None: continue
                    if ema50_mode == "true" and not bool(v): continue
                    if ema50_mode == "false" and bool(v): continue

                if ema9_mode != "ignore":
                    v = f.get("ema9_gt_ema21")
                    if v is None: continue
                    if ema9_mode == "true" and not bool(v): continue
                    if ema9_mode == "false" and bool(v): continue

                if macd_required == "true":
                    m = get("macd_histogram_pct")
                    if m is None or m <= 0: continue

                vd = get("volume_delta")
                if vd is None or vd < vol_delta_min: continue

                tr = get("taker_ratio")
                if tr is None or tr < taker_min: continue

                obp = get("orderbook_pressure")
                if obp is None or obp < obp_min: continue

                sp = get("spread_pct")
                if sp is None or sp > spread_max: continue

                matched.append(t)

            n = len(matched)
            if n < 20:
                return 0.0

            wins = sum(1 for t in matched if t["outcome"] == "TP_HIT")
            losses = sum(1 for t in matched if t["outcome"] == "SL_HIT")
            closed = wins + losses + sum(1 for t in matched if t["outcome"] == "TIMEOUT")
            win_rate = wins / max(closed, 1)
            avg_pnl = sum(t["pnl_pct"] for t in matched if t["outcome"] in ("TP_HIT","SL_HIT","TIMEOUT")) / max(closed, 1)
            tp30m = sum(1 for t in matched if t["outcome"] == "TP_HIT" and t["holding_seconds"] <= 1800) / max(closed, 1)
            avg_mae = sum(abs(t["mae_pct"] or 0) for t in matched) / max(n, 1)

            # Champion score
            from .profile_suggestion_service import calculate_champion_score
            metrics = {
                "win_rate": win_rate,
                "tp_30m_rate": tp30m,
                "avg_pnl_pct": avg_pnl,
                "avg_mae_pct": -avg_mae,
                "total_cases": n,
                "degradation_pct": None,
            }
            score = calculate_champion_score(metrics, base_metrics)

            # Penalty for overfit risk
            if n < 30: score *= 0.5
            if win_rate > 0.80 and n < 50: score *= 0.7

            return score

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, n_jobs=1, show_progress_bar=False)

        # Save top 5 trials
        from ..models.profile_intelligence import ProfileRuleCombination
        saved = []
        top_trials = sorted(study.trials, key=lambda t: t.value or 0, reverse=True)[:5]

        for trial in top_trials:
            if (trial.value or 0) < 20:
                continue
            params = trial.params
            rules = [
                {"indicator": "rsi", "operator": ">=", "value": params["rsi_min"]},
                {"indicator": "rsi", "operator": "<=", "value": params["rsi_max"]},
                {"indicator": "adx", "operator": ">=", "value": params["adx_min"]},
                {"indicator": "adx", "operator": "<=", "value": params["adx_max"]},
                {"indicator": "zscore", "operator": ">=", "value": params["zscore_min"]},
                {"indicator": "zscore", "operator": "<=", "value": params["zscore_max"]},
                {"indicator": "vwap_distance_pct", "operator": "<=", "value": params["vwap_max"]},
                {"indicator": "taker_ratio", "operator": ">=", "value": params["taker_min"]},
                {"indicator": "spread_pct", "operator": "<=", "value": params["spread_max"]},
            ]
            if params.get("ema50_mode") != "ignore":
                rules.append({"indicator": "ema50_gt_ema200", "operator": "==", "value": params["ema50_mode"] == "true"})
            if params.get("macd_required") == "true":
                rules.append({"indicator": "macd_histogram_pct", "operator": ">", "value": 0})

            discovery_matching, discovery_missing = _match_trades(trades, rules)
            validation_matching, validation_missing = _match_trades(
                validation_trades, rules
            )
            discovery_metrics = _window_metrics(
                discovery_matching,
                trades,
                discovery_start,
                discovery_end,
                discovery_missing,
            )
            validation_metrics = _window_metrics(
                validation_matching,
                validation_trades,
                validation_start,
                validation_end,
                validation_missing,
            )
            from .profile_validation_service import classify_validation
            classification = classify_validation(
                discovery_metrics=discovery_metrics,
                validation_metrics=validation_metrics,
                discovery_start=discovery_start,
                discovery_end=discovery_end,
                validation_start=validation_start,
                validation_end=validation_end,
                missing_count=discovery_missing + validation_missing,
            )
            optuna_status = _optuna_validation_status(classification)
            base_validation_metrics = _window_metrics(
                validation_trades,
                validation_trades,
                validation_start,
                validation_end,
                0,
            )
            validation_metrics.update({
                "validation_expected_pnl": validation_metrics["avg_pnl_pct"],
                "validation_precision": validation_metrics["win_rate"],
                "validation_fpr": 1.0 - validation_metrics["win_rate"],
                "validation_win_rate_lift": (
                    validation_metrics["win_rate"]
                    - validation_metrics["base_win_rate"]
                ),
                "validation_drawdown_reduction": (
                    abs(base_validation_metrics["avg_mae_pct"])
                    - abs(validation_metrics["avg_mae_pct"])
                ),
                "validation_trade_count": validation_metrics["total_cases"],
            })
            validation_metrics.update({
                **classification,
                "actionability_status": (
                    "validated"
                    if optuna_status == "optuna_validated"
                    else optuna_status
                ),
                "optuna_status": optuna_status,
                "best_trial_value_discovery": trial.value,
            })

            combo_hash = hashlib.sha256(
                f"optuna|{trial.number}|{user_id}|{run_id}".encode()
            ).hexdigest()[:32]
            from .algorithm_governance_service import source_profile_attribution
            source_profiles, source_profile_ids = source_profile_attribution(
                discovery_matching + validation_matching
            )

            c = ProfileRuleCombination(
                user_id=user_id,
                run_id=run_id,
                combination_hash=combo_hash,
                combination_type="optuna",
                setup_family="unknown",
                suggested_name=f"Optuna Trial #{trial.number} (score={trial.value:.1f})",
                rules_json=rules,
                source_profiles=source_profiles,
                source_profile_ids=source_profile_ids,
                champion_score=trial.value,
                total_cases=discovery_metrics["total_cases"],
                wins=discovery_metrics["wins"],
                losses=discovery_metrics["losses"],
                win_rate=discovery_metrics["win_rate"],
                avg_pnl_pct=discovery_metrics["avg_pnl_pct"],
                lift_vs_base=discovery_metrics["lift"],
                discovery_metrics_json=discovery_metrics,
                validation_metrics_json=validation_metrics,
                overfit_risk=optuna_status != "optuna_validated",
                status=validation_metrics["actionability_status"],
            )
            db.add(c)
            saved.append({
                "hash": combo_hash,
                "score": trial.value,
                "params": params,
                "discovery_metrics": discovery_metrics,
                "validation_metrics": validation_metrics,
                "status": optuna_status,
            })

        if saved:
            await db.flush()
        return saved
