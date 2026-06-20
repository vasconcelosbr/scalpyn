"""
Association Rules Engine — finds frequent itemsets and association rules.
Uses mlxtend if available, otherwise implements a lightweight fallback.
"""
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _association_actionability(
    consequents: List[str],
    validation_status: str,
) -> str:
    if validation_status != "validated":
        return "exploratory_only"
    if set(consequents) & {"LOSS", "SL_HIT"}:
        return "block_rule_candidate"
    if set(consequents) & {"WIN", "TP_HIT", "TP_15M", "TP_30M"}:
        return "positive_signal_candidate"
    if "TIMEOUT" in set(consequents):
        return "risk_warning"
    return "not_actionable"

# Attempt to import mlxtend
try:
    from mlxtend.frequent_patterns import apriori, association_rules
    from mlxtend.preprocessing import TransactionEncoder
    import pandas as pd
    _MLXTEND_AVAILABLE = True
except ImportError:
    _MLXTEND_AVAILABLE = False
    logger.info("[AssocRules] mlxtend not available — using fallback")


def _feature_to_items(features: dict, outcome: str, holding_seconds: Optional[float]) -> List[str]:
    """Convert a features dict + outcome to a list of transaction items."""
    items = []
    def _get(k):
        v = features.get(k)
        return float(v) if v is not None else None

    rsi = _get("rsi")
    if rsi is not None:
        if rsi < 30: items.append("RSI_LT_30")
        elif rsi < 38: items.append("RSI_30_38")
        elif rsi < 45: items.append("RSI_38_45")
        elif rsi < 55: items.append("RSI_45_55")
        elif rsi < 65: items.append("RSI_55_65")
        else: items.append("RSI_GTE_65")

    adx = _get("adx")
    if adx is not None:
        if adx < 18: items.append("ADX_LT_18")
        elif adx < 25: items.append("ADX_18_25")
        else: items.append("ADX_GTE_25")
        acc = _get("adx_acceleration")
        if acc is not None:
            items.append("ADX_WAKEUP" if acc > 0 else "ADX_COOLING")

    zscore = _get("zscore")
    if zscore is not None:
        if zscore < -1.5: items.append("ZSCORE_NEG")
        elif zscore <= 0.8: items.append("ZSCORE_NEUTRAL")
        elif zscore <= 1.5: items.append("ZSCORE_LE_1_5")
        else: items.append("ZSCORE_HIGH")

    macd = _get("macd_histogram_pct")
    if macd is not None:
        items.append("MACD_HIST_GT_0" if macd > 0 else "MACD_HIST_LTE_0")

    vd = _get("volume_delta")
    if vd is not None:
        items.append("VOLUME_DELTA_GE_0" if vd >= 0 else "VOLUME_DELTA_NEG")

    tr = _get("taker_ratio")
    if tr is not None:
        items.append("TAKER_RATIO_GE_055" if tr >= 0.55 else ("TAKER_RATIO_GE_045" if tr >= 0.45 else "TAKER_RATIO_LT_045"))

    ema50 = features.get("ema50_gt_ema200")
    if ema50 is not None:
        items.append("EMA50_GT_EMA200_TRUE" if bool(ema50) else "EMA50_GT_EMA200_FALSE")

    ema9 = features.get("ema9_gt_ema21")
    if ema9 is not None:
        items.append("EMA9_GT_EMA21_TRUE" if bool(ema9) else "EMA9_GT_EMA21_FALSE")

    vwap = _get("vwap_distance_pct")
    if vwap is not None:
        items.append("VWAP_LE_1_5" if vwap <= 1.5 else ("VWAP_1_5_2_5" if vwap <= 2.5 else "VWAP_GT_2_5"))

    spread = _get("spread_pct")
    if spread is not None:
        items.append("SPREAD_LE_020" if spread <= 0.20 else "SPREAD_GT_020")

    bp = _get("buy_pressure")
    if bp is not None and bp >= 0.55:
        items.append("BUY_PRESSURE_GE_055")

    obp = _get("orderbook_pressure")
    if obp is not None and obp >= 0.35:
        items.append("ORDERBOOK_PRESSURE_GE_035")

    vs = _get("volume_spike")
    if vs is not None and vs >= 1.2:
        items.append("VOLUME_SPIKE_GE_1_2")
    if vs is not None and vs >= 1.5:
        items.append("VOLUME_SPIKE_GE_1_5")

    # Outcome items
    if outcome == "TP_HIT":
        items.append("WIN")
        if holding_seconds and holding_seconds <= 900: items.append("TP_15M")
        if holding_seconds and holding_seconds <= 1800: items.append("TP_30M")
    elif outcome == "SL_HIT":
        items.append("LOSS")
        items.append("SL_HIT")

    return items


class AssociationRulesEngine:
    def __init__(self, min_support: float = 0.05, min_confidence: float = 0.55, min_lift: float = 1.1):
        self.min_support = min_support
        self.min_confidence = min_confidence
        self.min_lift = min_lift

    async def run(
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
    ) -> List[dict]:
        """
        Run association rules analysis. Returns list of discovered combinations.
        Saves qualifying rules to profile_rule_combinations.
        """
        async def load_transactions(start, end):
            rows = (await db.execute(text("""
            SELECT profile_id, profile_name, symbol, created_at, outcome,
                   holding_seconds, features_snapshot
            FROM shadow_trades
            WHERE user_id = :uid
              AND created_at >= :start
              AND created_at < :end
              AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
              AND features_snapshot IS NOT NULL
            ORDER BY created_at
            LIMIT 30000
            """), {"uid": str(user_id), "start": start, "end": end})).fetchall()
            loaded = []
            for row in rows:
                features = row.features_snapshot
                if isinstance(features, str):
                    import json
                    features = json.loads(features)
                if not features:
                    continue
                items = _feature_to_items(features, row.outcome, row.holding_seconds)
                if items:
                    loaded.append({
                        "items": set(items),
                        "profile_id": row.profile_id,
                        "profile_name": row.profile_name,
                        "symbol": row.symbol,
                        "created_at": row.created_at,
                    })
            return loaded

        discovery = await load_transactions(discovery_start, discovery_end)
        validation = await load_transactions(validation_start, validation_end)
        if len(discovery) < 30:
            logger.info("[AssocRules] Insufficient trades (%d) for user=%s", len(discovery), user_id)
            return []

        transactions = [row["items"] for row in discovery]

        if _MLXTEND_AVAILABLE:
            return await self._run_mlxtend(
                db, user_id, run_id, base_metrics, transactions,
                validation, discovery_start, discovery_end,
                validation_start, validation_end,
            )
        else:
            return await self._run_fallback(
                db, user_id, run_id, base_metrics, transactions,
                validation, discovery_start, discovery_end,
                validation_start, validation_end,
            )

    async def _run_mlxtend(
        self, db, user_id, run_id, base_metrics, transactions,
        validation, discovery_start, discovery_end,
        validation_start, validation_end,
    ):
        import pandas as pd
        from mlxtend.frequent_patterns import apriori, association_rules
        from mlxtend.preprocessing import TransactionEncoder

        te = TransactionEncoder()
        te_array = te.fit(transactions).transform(transactions)
        df = pd.DataFrame(te_array, columns=te.columns_)

        frequent = apriori(df, min_support=self.min_support, use_colnames=True)
        if frequent.empty:
            return []

        rules = association_rules(frequent, metric="confidence", min_threshold=self.min_confidence)
        rules = rules[rules["lift"] >= self.min_lift]

        outcome_items = {"WIN", "LOSS", "SL_HIT", "TP_15M", "TP_30M"}
        winning_rules = rules[rules["consequents"].apply(lambda x: bool(x & outcome_items))]

        return await self._save_rules(
            db, user_id, run_id, base_metrics, winning_rules.head(50),
            validation, len(transactions), discovery_start, discovery_end,
            validation_start, validation_end,
        )

    async def _run_fallback(
        self, db, user_id, run_id, base_metrics, transactions,
        validation, discovery_start, discovery_end,
        validation_start, validation_end,
    ):
        """Simple co-occurrence counting fallback."""
        from itertools import combinations
        from collections import defaultdict

        n = len(transactions)
        item_counts: dict = defaultdict(int)
        pair_counts: dict = defaultdict(int)
        outcome_items = {"WIN", "LOSS", "TP_15M", "TP_30M", "SL_HIT"}

        for t in transactions:
            feature_items = t - outcome_items
            outcome = t & outcome_items
            for item in feature_items:
                item_counts[item] += 1
            for item in feature_items:
                for o in outcome:
                    pair_counts[(item, o)] += 1

        results = []
        for (item, outcome_item), co_count in pair_counts.items():
            antecedent_count = item_counts.get(item, 0)
            if antecedent_count < self.min_support * n:
                continue
            conf = co_count / max(antecedent_count, 1)
            support = antecedent_count / n
            outcome_count = sum(1 for t in transactions if outcome_item in t)
            expected = antecedent_count * outcome_count / n
            lift = co_count / max(expected, 0.001)
            if conf >= self.min_confidence and lift >= self.min_lift:
                results.append({
                    "antecedents": frozenset([item]),
                    "consequents": frozenset([outcome_item]),
                    "support": support,
                    "confidence": conf,
                    "lift": lift,
                })

        results.sort(key=lambda x: x["lift"], reverse=True)
        return await self._save_fallback_rules(
            db, user_id, run_id, base_metrics, results[:30],
            validation, len(transactions), discovery_start, discovery_end,
            validation_start, validation_end,
        )

    def _validation_metrics(self, validation, antecedents, consequents):
        from .profile_validation_service import diversity_metrics

        total = len(validation)
        antecedent_rows = [
            row for row in validation
            if set(antecedents).issubset(row["items"])
        ]
        matched = [
            row for row in antecedent_rows
            if set(consequents).issubset(row["items"])
        ]
        outcome_count = sum(
            1 for row in validation
            if set(consequents).issubset(row["items"])
        )
        antecedent_count = len(antecedent_rows)
        co_count = len(matched)
        confidence = co_count / max(antecedent_count, 1)
        support = co_count / max(total, 1)
        outcome_rate = outcome_count / max(total, 1)
        lift = confidence / max(outcome_rate, 0.001)
        metrics = {
            "total_cases": antecedent_count,
            "trade_count": antecedent_count,
            "support": support,
            "confidence": confidence,
            "lift": lift,
            # For association rules this pair represents target-event rate
            # versus its unconditional base rate. It works for both WIN and
            # LOSS consequents without misclassifying LOSS as a positive signal.
            "win_rate": confidence,
            "base_win_rate": outcome_rate,
            "target_event_rate": confidence,
            "target_base_rate": outcome_rate,
            **diversity_metrics(antecedent_rows),
        }
        return metrics

    async def _save_rules(
        self, db, user_id, run_id, base_metrics, rules_df,
        validation, discovery_count, discovery_start, discovery_end,
        validation_start, validation_end,
    ):
        """Save mlxtend rules to profile_rule_combinations."""
        from ..models.profile_intelligence import ProfileRuleCombination
        saved = []
        for _, row in rules_df.iterrows():
            antecedents = list(row["antecedents"])
            consequents = list(row["consequents"])
            val_metrics = self._validation_metrics(
                validation, antecedents, consequents
            )
            discovery_support = float(row.get("support", 0))
            disc_metrics = {
                "start": discovery_start.isoformat(),
                "end": discovery_end.isoformat(),
                "total_cases": int(discovery_support * discovery_count),
                "trade_count": int(discovery_support * discovery_count),
                "support": discovery_support,
                "confidence": float(row.get("confidence", 0)),
                "lift": float(row.get("lift", 0)),
            }
            from .profile_validation_service import classify_validation
            classification = classify_validation(
                discovery_metrics=disc_metrics,
                validation_metrics=val_metrics,
                discovery_start=discovery_start,
                discovery_end=discovery_end,
                validation_start=validation_start,
                validation_end=validation_end,
                association_rule=True,
            )
            val_metrics.update({
                "start": validation_start.isoformat(),
                "end": validation_end.isoformat(),
                "antecedents": antecedents,
                "consequents": consequents,
            })
            classification["actionability_status"] = _association_actionability(
                consequents,
                classification["validation_status"],
            )
            val_metrics.update(classification)
            rules_json = [{"item": a} for a in antecedents]
            from .algorithm_governance_service import source_profile_attribution
            source_profiles, source_profile_ids = source_profile_attribution(
                [
                    item for item in validation
                    if set(antecedents).issubset(item["items"])
                ]
            )
            combo_hash = hashlib.sha256(
                f"assoc|{'|'.join(sorted(antecedents))}|{'|'.join(sorted(consequents))}|{user_id}".encode()
            ).hexdigest()[:32]

            combination = ProfileRuleCombination(
                user_id=user_id,
                run_id=run_id,
                combination_hash=combo_hash,
                combination_type="association_rule",
                setup_family="unknown",
                suggested_name=f"AR: {' + '.join(antecedents[:3])} → {', '.join(consequents)}",
                rules_json=rules_json,
                source_profiles=source_profiles,
                source_profile_ids=source_profile_ids,
                support=float(row.get("support", 0)),
                confidence=float(row.get("confidence", 0)),
                rule_lift=float(row.get("lift", 0)),
                leverage=float(row.get("leverage", 0)) if "leverage" in row else None,
                conviction=float(row.get("conviction", 0)) if "conviction" in row else None,
                discovery_metrics_json=disc_metrics,
                validation_metrics_json=val_metrics,
                status=classification["actionability_status"],
            )
            db.add(combination)
            saved.append({
                "hash": combo_hash,
                "antecedents": antecedents,
                "consequents": consequents,
                **classification,
            })

        if saved:
            await db.flush()
        return saved

    async def _save_fallback_rules(
        self, db, user_id, run_id, base_metrics, results,
        validation, discovery_count, discovery_start, discovery_end,
        validation_start, validation_end,
    ):
        """Save fallback rules to profile_rule_combinations."""
        from ..models.profile_intelligence import ProfileRuleCombination
        saved = []
        for r in results:
            antecedents = list(r["antecedents"])
            consequents = list(r["consequents"])
            val_metrics = self._validation_metrics(
                validation, antecedents, consequents
            )
            discovery_support = float(r["support"])
            disc_metrics = {
                "start": discovery_start.isoformat(),
                "end": discovery_end.isoformat(),
                "total_cases": int(discovery_support * discovery_count),
                "trade_count": int(discovery_support * discovery_count),
                "support": discovery_support,
                "confidence": r["confidence"],
                "lift": r["lift"],
            }
            from .profile_validation_service import classify_validation
            classification = classify_validation(
                discovery_metrics=disc_metrics,
                validation_metrics=val_metrics,
                discovery_start=discovery_start,
                discovery_end=discovery_end,
                validation_start=validation_start,
                validation_end=validation_end,
                association_rule=True,
            )
            val_metrics.update({
                "start": validation_start.isoformat(),
                "end": validation_end.isoformat(),
                "antecedents": antecedents,
                "consequents": consequents,
            })
            classification["actionability_status"] = _association_actionability(
                consequents,
                classification["validation_status"],
            )
            val_metrics.update(classification)
            rules_json = [{"item": a} for a in antecedents]
            from .algorithm_governance_service import source_profile_attribution
            source_profiles, source_profile_ids = source_profile_attribution(
                [
                    item for item in validation
                    if set(antecedents).issubset(item["items"])
                ]
            )
            combo_hash = hashlib.sha256(
                f"assoc|{'|'.join(sorted(antecedents))}|{'|'.join(sorted(consequents))}|{user_id}".encode()
            ).hexdigest()[:32]

            combination = ProfileRuleCombination(
                user_id=user_id,
                run_id=run_id,
                combination_hash=combo_hash,
                combination_type="association_rule",
                setup_family="unknown",
                suggested_name=f"AR(fb): {' + '.join(antecedents[:2])} → {', '.join(consequents)}",
                rules_json=rules_json,
                source_profiles=source_profiles,
                source_profile_ids=source_profile_ids,
                support=r["support"],
                confidence=r["confidence"],
                rule_lift=r["lift"],
                discovery_metrics_json=disc_metrics,
                validation_metrics_json=val_metrics,
                status=classification["actionability_status"],
            )
            db.add(combination)
            saved.append({
                "hash": combo_hash,
                "antecedents": antecedents,
                "consequents": consequents,
                **classification,
            })

        if saved:
            await db.flush()
        return saved
