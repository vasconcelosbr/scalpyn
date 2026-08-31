"""Read-only production gates for the Shadow L3 barrier rollout.

M1 measures barrier executability only where closed 1m OHLCV coverage is
complete. M2 simulates the persisted global RSI range rule without changing
decisions. M3 ranks active L3 profiles by recent natural event throughput and
technical coverage. The transaction is explicitly read-only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg2
from psycopg2.extras import RealDictCursor


TERMINAL_OUTCOMES = ("TP_HIT", "SL_HIT", "TRAILING_STOP", "TIMEOUT")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    data = [float(value) for value in values]
    return {
        "n": len(data),
        "p50": _percentile(data, 0.50),
        "p90": _percentile(data, 0.90),
        "p95": _percentile(data, 0.95),
        "max": max(data) if data else None,
    }


def _minute_bucket(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _first(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    source = mapping or {}
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _global_rsi_rule(config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    blocks = ((config or {}).get("block_rules") or {}).get("blocks") or []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_id = str(block.get("id") or "").lower()
        block_name = str(block.get("name") or "").lower()
        indicator = str(block.get("indicator") or "").lower()
        if block_id == "b1" or indicator == "rsi" or "rsi" in block_name:
            return dict(block)
    return None


def _b1_evaluation(metrics: Mapping[str, Any] | None) -> dict[str, Any] | None:
    gate = (metrics or {}).get("l3_gate_v2") or {}
    block_rules = gate.get("block_rules") or {}
    for rule in block_rules.get("evaluated") or block_rules.get("rules") or []:
        if not isinstance(rule, Mapping):
            continue
        rule_id = str(rule.get("id") or "").lower()
        rule_name = str(rule.get("name") or "").lower()
        if rule_id != "b1" and "rsi" not in rule_name:
            continue
        for condition in rule.get("conditions") or []:
            if not isinstance(condition, Mapping):
                continue
            if str(condition.get("indicator") or "").lower() == "rsi":
                return dict(condition)
    return None


def _processing_result(payload: Mapping[str, Any] | None) -> str | None:
    result = (payload or {}).get("processing_result")
    return str(result) if result else None


def _config_by_user(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row['user_id']}:{row['config_type']}"
        if key not in selected:
            selected[key] = dict(row.get("config_json") or {})
    return selected


def collect(connection: Any) -> dict[str, Any]:
    connection.set_session(readonly=True, autocommit=False)
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SET LOCAL statement_timeout = '120000ms'")
        cursor.execute("SET LOCAL lock_timeout = '5000ms'")
        cursor.execute("SET LOCAL temp_file_limit = '128MB'")

        cursor.execute(
            """
            SELECT NOW() AS database_now,
                   current_database() AS database_name,
                   current_setting('transaction_read_only') AS transaction_read_only,
                   (SELECT version_num FROM alembic_version) AS alembic_version
            """
        )
        schema = dict(cursor.fetchone())
        database_now = schema["database_now"]

        cursor.execute(
            """
            SELECT user_id, config_type, config_json, id, updated_at
              FROM config_profiles
             WHERE is_active IS TRUE
               AND pool_id IS NULL
               AND config_type IN ('ml', 'block')
             ORDER BY user_id, config_type, updated_at DESC, id DESC
            """
        )
        config_rows = list(cursor.fetchall())
        configs = _config_by_user(config_rows)

        cursor.execute(
            """
            SELECT DISTINCT p.id AS profile_id, p.user_id, p.name AS profile_name
              FROM profiles p
              JOIN pipeline_watchlists w
                ON w.profile_id = p.id
               AND upper(w.level) = 'L3'
               AND w.auto_refresh IS TRUE
             WHERE p.is_active IS TRUE
             ORDER BY p.id
            """
        )
        active_profiles = [dict(row) for row in cursor.fetchall()]
        profile_lookup = {
            str(row["profile_id"]): row for row in active_profiles
        }

        cursor.execute(
            """
            SELECT MIN(time) AS first_at, MAX(time) AS last_at,
                   COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols
              FROM ohlcv
             WHERE timeframe = '1m'
            """
        )
        retention = dict(cursor.fetchone())

        cursor.execute(
            """
            SELECT COUNT(*) AS total_terminal
              FROM shadow_trades
             WHERE source = 'L3'
               AND strategy_type = 'PROFILE_L3'
               AND status = 'COMPLETED'
               AND outcome = ANY(%s)
            """,
            (list(TERMINAL_OUTCOMES),),
        )
        total_terminal = int(cursor.fetchone()["total_terminal"] or 0)

        cursor.execute(
            """
            SELECT id, user_id, profile_id, profile_name, symbol,
                   entry_price, entry_timestamp, exit_timestamp,
                   tp_price, sl_price, tp_pct, sl_pct,
                   tp_pct_applied, sl_pct_applied, atr_pct_at_entry,
                   config_snapshot, outcome
              FROM shadow_trades
             WHERE source = 'L3'
               AND strategy_type = 'PROFILE_L3'
               AND status = 'COMPLETED'
               AND outcome = ANY(%s)
               AND entry_price IS NOT NULL
               AND entry_price > 0
               AND entry_timestamp IS NOT NULL
               AND exit_timestamp IS NOT NULL
               AND entry_timestamp >= %s
               AND exit_timestamp < %s + INTERVAL '1 minute'
             ORDER BY entry_timestamp, id
            """,
            (list(TERMINAL_OUTCOMES), retention["first_at"], retention["last_at"]),
        )
        trades = [dict(row) for row in cursor.fetchall()]

        m1_rows: list[dict[str, Any]] = []
        coverage_counts: Counter[str] = Counter()
        exclusions: Counter[str] = Counter()
        for trade in trades:
            entry_at = trade["entry_timestamp"]
            exit_at = trade["exit_timestamp"]
            entry_bucket = _minute_bucket(entry_at)
            exit_bucket = _minute_bucket(exit_at)
            expected = int((exit_bucket - entry_bucket).total_seconds() // 60) + 1
            user_key = str(trade["user_id"])
            ml_config = configs.get(f"{user_key}:ml") or {}
            tp_mult = _finite(ml_config.get("shadow_atr_multiplier_tp"))
            sl_mult = _finite(ml_config.get("shadow_atr_multiplier_sl"))
            ratio = tp_mult / sl_mult if tp_mult and sl_mult else None
            applied_sl_pct = _finite(trade.get("sl_pct_applied"))
            sl_pct_source = "sl_pct_applied"
            if applied_sl_pct is None:
                applied_sl_pct = _finite(trade.get("sl_pct"))
                sl_pct_source = "sl_pct"
            if applied_sl_pct is None and trade.get("sl_price") is not None:
                applied_sl_pct = abs(
                    (float(trade["sl_price"]) / float(trade["entry_price"]) - 1.0)
                    * 100.0
                )
                sl_pct_source = "derived_from_persisted_prices"
            ratio_target = (
                float(trade["entry_price"]) * (1.0 + applied_sl_pct * ratio / 100.0)
                if applied_sl_pct is not None and ratio is not None
                else None
            )
            cursor.execute(
                """
                SELECT MIN(time) AS first_at, MAX(time) AS last_at,
                       COUNT(DISTINCT time) AS candle_count,
                       MAX(high) AS max_high,
                       MAX(high) FILTER (WHERE time > %s) AS max_high_after_entry_bucket,
                       MIN(time) FILTER (WHERE %s IS NOT NULL AND high >= %s) AS ratio_touch_at,
                       MIN(time) FILTER (WHERE tp_price_cmp IS TRUE) AS tp_touch_at,
                       MIN(time) FILTER (WHERE sl_price_cmp IS TRUE) AS sl_touch_at,
                       MIN(time) FILTER (
                           WHERE tp_price_cmp IS TRUE AND sl_price_cmp IS TRUE
                       ) AS both_same_candle_at
                  FROM (
                    SELECT time, high, low,
                           (%s IS NOT NULL AND high >= %s) AS tp_price_cmp,
                           (%s IS NOT NULL AND low <= %s) AS sl_price_cmp
                      FROM ohlcv
                     WHERE symbol = %s
                       AND timeframe = '1m'
                       AND time >= %s
                       AND time <= %s
                  ) candles
                """,
                (
                    entry_bucket,
                    ratio_target,
                    ratio_target,
                    trade.get("tp_price"),
                    trade.get("tp_price"),
                    trade.get("sl_price"),
                    trade.get("sl_price"),
                    trade["symbol"],
                    entry_bucket,
                    exit_bucket,
                ),
            )
            candle = dict(cursor.fetchone())
            count = int(candle.get("candle_count") or 0)
            if count == 0:
                coverage = "ABSENT"
            elif (
                candle.get("first_at") == entry_bucket
                and candle.get("last_at") == exit_bucket
                and count == expected
            ):
                coverage = "COMPLETE"
            else:
                coverage = "PARTIAL"
            coverage_counts[coverage] += 1

            snapshot = trade.get("config_snapshot") or {}
            atr = _finite(trade.get("atr_pct_at_entry"))
            if atr is None:
                atr = _finite(snapshot.get("barrier_atr_pct_used"))
            snap_sl_mult = _finite(
                _first(snapshot, "sl_atr_multiplier", "shadow_atr_multiplier_sl")
            )
            snap_floor = _finite(
                _first(snapshot, "clamp_min", "sl_min_pct", "shadow_barrier_min_pct")
            )
            historical_floor_bite = (
                atr * snap_sl_mult < snap_floor
                if atr is not None and snap_sl_mult is not None and snap_floor is not None
                else None
            )
            current_floor = _finite(ml_config.get("shadow_barrier_min_pct"))
            current_floor_bite = (
                atr * sl_mult < current_floor
                if atr is not None and sl_mult is not None and current_floor is not None
                else None
            )

            ratio_touch = candle.get("ratio_touch_at")
            tp_touch = candle.get("tp_touch_at")
            sl_touch = candle.get("sl_touch_at")
            ratio_unresolved = bool(
                ratio_touch == entry_bucket or sl_touch == entry_bucket
            )
            tp_unresolved = bool(tp_touch == entry_bucket or sl_touch == entry_bucket)

            def before_sl(touch: datetime | None, unresolved: bool) -> bool | None:
                if coverage != "COMPLETE" or unresolved or touch is None:
                    return None
                if sl_touch is None:
                    return True
                return touch < sl_touch

            max_high = _finite(candle.get("max_high"))
            max_high_after = _finite(candle.get("max_high_after_entry_bucket"))
            mfe = (
                (max_high / float(trade["entry_price"]) - 1.0) * 100.0
                if max_high is not None
                else None
            )
            mfe_after = (
                (max_high_after / float(trade["entry_price"]) - 1.0) * 100.0
                if max_high_after is not None
                else None
            )
            if coverage != "COMPLETE":
                exclusions[f"OHLCV_{coverage}"] += 1
            if ratio is None:
                exclusions["CURRENT_RATIO_UNCONFIGURED"] += 1
            if historical_floor_bite is None:
                exclusions["HISTORICAL_FLOOR_BITE_INPUT_MISSING"] += 1
            if current_floor_bite is None:
                exclusions["CURRENT_POLICY_FLOOR_BITE_INPUT_MISSING"] += 1
            m1_rows.append(
                {
                    "shadow_trade_id": str(trade["id"]),
                    "profile_id": str(trade["profile_id"]) if trade.get("profile_id") else None,
                    "profile_name": trade.get("profile_name"),
                    "symbol": trade["symbol"],
                    "outcome": trade["outcome"],
                    "coverage": coverage,
                    "expected_candles": expected,
                    "observed_candles": count,
                    "entry_bucket": entry_bucket,
                    "exit_bucket": exit_bucket,
                    "entry_boundary_partial": entry_at != entry_bucket,
                    "current_ratio": ratio,
                    "sl_pct": applied_sl_pct,
                    "sl_pct_source": sl_pct_source,
                    "ratio_target_pct": applied_sl_pct * ratio if applied_sl_pct is not None and ratio is not None else None,
                    "ratio_touch_at": ratio_touch,
                    "ratio_path_unresolved": ratio_unresolved,
                    "ratio_reached_before_sl": before_sl(ratio_touch, ratio_unresolved),
                    "v2_tp_touch_at": tp_touch,
                    "v2_tp_path_unresolved": tp_unresolved,
                    "v2_tp_reached_before_sl": before_sl(tp_touch, tp_unresolved),
                    "sl_touch_at": sl_touch,
                    "both_same_candle_at": candle.get("both_same_candle_at"),
                    "mfe_pct_overlapping": mfe,
                    "mfe_pct_closed_after_entry_bucket": mfe_after,
                    "atr_pct_at_entry": atr,
                    "historical_floor_bite": historical_floor_bite,
                    "current_policy_floor_bite": current_floor_bite,
                    "current_policy_sl_atr_multiplier": sl_mult,
                    "current_policy_floor_pct": current_floor,
                }
            )

        complete = [row for row in m1_rows if row["coverage"] == "COMPLETE"]
        decidable_ratio = [
            row for row in complete if row["ratio_reached_before_sl"] is not None
        ]
        decidable_v2 = [
            row for row in complete if row["v2_tp_reached_before_sl"] is not None
        ]
        by_profile_m1: list[dict[str, Any]] = []
        for profile_id in sorted({row.get("profile_id") for row in m1_rows}):
            rows = [row for row in m1_rows if row.get("profile_id") == profile_id]
            complete_rows = [row for row in rows if row["coverage"] == "COMPLETE"]
            ratio_rows = [row for row in complete_rows if row["ratio_reached_before_sl"] is not None]
            v2_rows = [row for row in complete_rows if row["v2_tp_reached_before_sl"] is not None]
            by_profile_m1.append(
                {
                    "profile_id": profile_id,
                    "profile_name": next((row.get("profile_name") for row in rows if row.get("profile_name")), None),
                    "candidate_trades": len(rows),
                    "coverage": dict(Counter(row["coverage"] for row in rows)),
                    "ratio_decidable": len(ratio_rows),
                    "ratio_reached_before_sl": sum(row["ratio_reached_before_sl"] is True for row in ratio_rows),
                    "v2_decidable": len(v2_rows),
                    "v2_tp_reached_before_sl": sum(row["v2_tp_reached_before_sl"] is True for row in v2_rows),
                    "mfe_pct_closed_after_entry_bucket": _distribution(
                        row["mfe_pct_closed_after_entry_bucket"]
                        for row in complete_rows
                        if row["mfe_pct_closed_after_entry_bucket"] is not None
                    ),
                    "historical_floor_bite_known": sum(
                        row["historical_floor_bite"] is not None for row in complete_rows
                    ),
                    "historical_floor_bite": sum(
                        row["historical_floor_bite"] is True for row in complete_rows
                    ),
                    "current_policy_floor_bite_known": sum(
                        row["current_policy_floor_bite"] is not None for row in complete_rows
                    ),
                    "current_policy_floor_bite": sum(
                        row["current_policy_floor_bite"] is True for row in complete_rows
                    ),
                    "both_same_candle": sum(row["both_same_candle_at"] is not None for row in complete_rows),
                }
            )

        atr_values = sorted(
            row["atr_pct_at_entry"]
            for row in complete
            if row["atr_pct_at_entry"] is not None
        )
        atr_edges = {
            "p25": _percentile(atr_values, 0.25),
            "p50": _percentile(atr_values, 0.50),
            "p75": _percentile(atr_values, 0.75),
        }
        atr_bands: list[dict[str, Any]] = []
        if atr_values and all(value is not None for value in atr_edges.values()):
            edges = [atr_edges["p25"], atr_edges["p50"], atr_edges["p75"]]
            labels = ("Q1", "Q2", "Q3", "Q4")
            buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in complete:
                atr = row["atr_pct_at_entry"]
                if atr is None:
                    continue
                index = sum(atr > edge for edge in edges)
                buckets[labels[index]].append(row)
            for label in labels:
                rows = buckets.get(label, [])
                ratio_rows = [row for row in rows if row["ratio_reached_before_sl"] is not None]
                atr_bands.append(
                    {
                        "band": label,
                        "n": len(rows),
                        "ratio_decidable": len(ratio_rows),
                        "ratio_reached_before_sl": sum(row["ratio_reached_before_sl"] is True for row in ratio_rows),
                        "mfe_pct_closed_after_entry_bucket": _distribution(
                            row["mfe_pct_closed_after_entry_bucket"]
                            for row in rows
                            if row["mfe_pct_closed_after_entry_bucket"] is not None
                        ),
                    }
                )

        cursor.execute(
            """
            SELECT id, user_id, profile_id, profile_name, decision, l3_pass,
                   metrics, created_at
              FROM decisions_log
             WHERE metrics ? 'l3_gate_v2'
             ORDER BY id
            """
        )
        decisions = [dict(row) for row in cursor.fetchall()]
        m2_by_profile: dict[str, Counter[str]] = defaultdict(Counter)
        m2_total: Counter[str] = Counter()
        for decision in decisions:
            profile_id = str(decision["profile_id"]) if decision.get("profile_id") else "NONE"
            counters = m2_by_profile[profile_id]
            m2_total["decisions"] += 1
            counters["decisions"] += 1
            evaluation = _b1_evaluation(decision.get("metrics"))
            if evaluation is None:
                m2_total["rsi_missing"] += 1
                counters["rsi_missing"] += 1
                continue
            actual = _finite(evaluation.get("actual"))
            if actual is None:
                m2_total["rsi_invalid"] += 1
                counters["rsi_invalid"] += 1
                continue
            m2_total["rsi_valid_numeric"] += 1
            counters["rsi_valid_numeric"] += 1
            source_at = _first(
                evaluation, "source_at", "source_timestamp", "observed_at", "time"
            )
            if source_at is None:
                m2_total["rsi_timestamp_missing"] += 1
                counters["rsi_timestamp_missing"] += 1
            else:
                m2_total["rsi_timestamp_present"] += 1
                counters["rsi_timestamp_present"] += 1
            user_id = str(
                (profile_lookup.get(profile_id) or {}).get("user_id")
                or decision.get("user_id")
                or ""
            )
            block_config = configs.get(f"{user_id}:block") or {}
            rule = _global_rsi_rule(block_config)
            min_value = _finite((rule or {}).get("min"))
            max_value = _finite((rule or {}).get("max"))
            if min_value is None or max_value is None:
                m2_total["threshold_unconfigured"] += 1
                counters["threshold_unconfigured"] += 1
                continue
            would_block = actual < min_value or actual > max_value
            m2_total["would_block"] += int(would_block)
            counters["would_block"] += int(would_block)
            m2_total["would_allow"] += int(not would_block)
            counters["would_allow"] += int(not would_block)

        m2_profiles = []
        for profile_id, counters in sorted(m2_by_profile.items()):
            m2_profiles.append(
                {
                    "profile_id": None if profile_id == "NONE" else profile_id,
                    "profile_name": (profile_lookup.get(profile_id) or {}).get("profile_name"),
                    **dict(counters),
                }
            )

        window_start = database_now - timedelta(days=7)
        recent_decisions = [
            decision for decision in decisions if decision["created_at"] >= window_start
        ]
        decision_ids = [int(row["id"]) for row in recent_decisions]
        recent_shadows: list[dict[str, Any]] = []
        outbox_rows: list[dict[str, Any]] = []
        if decision_ids:
            cursor.execute(
                """
                SELECT id, decision_id, profile_id, status, outcome,
                       entry_timestamp, exit_timestamp
                  FROM shadow_trades
                 WHERE decision_id = ANY(%s)
                   AND source = 'L3'
                   AND strategy_type = 'PROFILE_L3'
                """,
                (decision_ids,),
            )
            recent_shadows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT decision_id, status, payload, last_error, processed_at
                  FROM l3_authorization_outbox
                 WHERE decision_id = ANY(%s)
                 ORDER BY decision_id, created_at
                """,
                (decision_ids,),
            )
            outbox_rows = [dict(row) for row in cursor.fetchall()]

        shadows_by_decision: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in recent_shadows:
            shadows_by_decision[int(row["decision_id"])].append(row)
        outbox_by_decision: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in outbox_rows:
            outbox_by_decision[int(row["decision_id"])].append(row)

        complete_shadow_ids = {
            row["shadow_trade_id"] for row in complete
        }
        m3_rows: list[dict[str, Any]] = []
        for profile in active_profiles:
            profile_id = str(profile["profile_id"])
            rows = [
                row for row in recent_decisions if str(row.get("profile_id")) == profile_id
            ]
            l3_pass_rows = [row for row in rows if row.get("l3_pass") is True]
            shadows = [
                shadow
                for row in rows
                for shadow in shadows_by_decision.get(int(row["id"]), [])
            ]
            shadow_ids = {str(row["id"]) for row in shadows}
            suppressed = 0
            without_terminal = 0
            for row in l3_pass_rows:
                events = outbox_by_decision.get(int(row["id"]), [])
                results = [_processing_result(event.get("payload")) for event in events]
                if any(result and result.startswith("SUPPRESSED/") for result in results):
                    suppressed += 1
                terminal = any(
                    event.get("status") == "PROCESSED" and _processing_result(event.get("payload"))
                    for event in events
                )
                if not terminal:
                    without_terminal += 1
            m3_rows.append(
                {
                    "profile_id": profile_id,
                    "profile_name": profile["profile_name"],
                    "decisions": len(rows),
                    "l3_pass": len(l3_pass_rows),
                    "shadows": len(shadow_ids),
                    "terminal_outcomes": sum(row.get("outcome") in TERMINAL_OUTCOMES for row in shadows),
                    "suppressed": suppressed,
                    "l3_pass_without_terminal_outbox": without_terminal,
                    "complete_1m_coverage": sum(shadow_id in complete_shadow_ids for shadow_id in shadow_ids),
                }
            )
        viable = [
            row for row in m3_rows
            if row["l3_pass"] > 0
            and row["complete_1m_coverage"] > 0
            and row["shadows"] > 0
        ]
        viable.sort(
            key=lambda row: (
                -row["complete_1m_coverage"],
                -row["terminal_outcomes"],
                -row["shadows"],
                -row["l3_pass"],
                row["profile_id"],
            )
        )

        cursor.execute(
            """
            SELECT entry_risk_capture_status,
                   entry_risk_features_json ->> 'schema_version' AS schema_version,
                   entry_risk_features_json #>> '{contract_status,status}' AS contract_status,
                   COUNT(*) AS rows
              FROM shadow_trades
             WHERE source = 'L3'
             GROUP BY entry_risk_capture_status, schema_version, contract_status
             ORDER BY rows DESC, entry_risk_capture_status
            """
        )
        entry_risk_status = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT reason_code, COUNT(*) AS occurrences
              FROM shadow_trades st
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  COALESCE(
                      st.entry_risk_features_json #> '{contract_status,reason_codes}',
                      '[]'::jsonb
                  )
              ) AS reason_code
             WHERE st.source = 'L3'
             GROUP BY reason_code
             ORDER BY occurrences DESC, reason_code
            """
        )
        entry_risk_reasons = [dict(row) for row in cursor.fetchall()]

        report = {
            "contract": "shadow_barrier_rollout_audit_v1",
            "generated_at": database_now,
            "execution": {
                "read_only": schema["transaction_read_only"] == "on",
                "statement_timeout_ms": 120000,
                "source": "production_database",
            },
            "schema": schema,
            "active_profiles": {
                "count": len(active_profiles),
                "profiles": active_profiles,
            },
            "m1": {
                "gate": "GEOMETRY_ACTIVATION",
                "ohlcv_retention_1m": retention,
                "total_terminal_shadows": total_terminal,
                "retention_candidate_shadows": len(trades),
                "coverage": dict(coverage_counts),
                "exclusions": dict(exclusions),
                "current_ratio_decidable": len(decidable_ratio),
                "current_ratio_reached_before_sl": sum(
                    row["ratio_reached_before_sl"] is True for row in decidable_ratio
                ),
                "v2_decidable": len(decidable_v2),
                "v2_tp_reached_before_sl": sum(
                    row["v2_tp_reached_before_sl"] is True for row in decidable_v2
                ),
                "mfe_pct_overlapping": _distribution(
                    row["mfe_pct_overlapping"]
                    for row in complete
                    if row["mfe_pct_overlapping"] is not None
                ),
                "mfe_pct_closed_after_entry_bucket": _distribution(
                    row["mfe_pct_closed_after_entry_bucket"]
                    for row in complete
                    if row["mfe_pct_closed_after_entry_bucket"] is not None
                ),
                "historical_floor_bite_known": sum(
                    row["historical_floor_bite"] is not None for row in complete
                ),
                "historical_floor_bite": sum(
                    row["historical_floor_bite"] is True for row in complete
                ),
                "current_policy_floor_bite_known": sum(
                    row["current_policy_floor_bite"] is not None for row in complete
                ),
                "current_policy_floor_bite": sum(
                    row["current_policy_floor_bite"] is True for row in complete
                ),
                "both_same_candle": sum(row["both_same_candle_at"] is not None for row in complete),
                "entry_path_unresolved_ratio": sum(row["ratio_path_unresolved"] for row in complete),
                "entry_path_unresolved_v2": sum(row["v2_tp_path_unresolved"] for row in complete),
                "atr_quantile_edges": atr_edges,
                "by_atr_quantile": atr_bands,
                "by_profile": by_profile_m1,
                "rows": m1_rows,
            },
            "m2": {
                "gate": "GLOBAL_B1_ACTIVATION",
                "aggregate": dict(m2_total),
                "freshness_status": (
                    "NOT_EVALUABLE_WITHOUT_SOURCE_TIMESTAMP_AND_FRESHNESS_POLICY"
                    if m2_total["rsi_timestamp_missing"]
                    else "SOURCE_TIMESTAMPS_PRESENT"
                ),
                "by_profile": m2_profiles,
            },
            "m3": {
                "gate": "CANARY_SELECTION",
                "window_start": window_start,
                "window_end": database_now,
                "ranking_policy": [
                    "complete_1m_coverage_desc",
                    "terminal_outcomes_desc",
                    "shadows_desc",
                    "l3_pass_desc",
                    "profile_id_asc",
                ],
                "profiles": m3_rows,
                "selected_profile": viable[0] if viable else None,
            },
            "entry_risk_readiness": {
                "status_counts": entry_risk_status,
                "reason_code_occurrences": entry_risk_reasons,
                "activation_status": "MONITOR_ONLY_UNTIL_V2_SOURCE_POLICIES_ARE_CONFIGURED",
            },
            "activation_gates": {
                "m1_measurement_available": bool(complete),
                "m2_measurement_available": m2_total["rsi_valid_numeric"] > 0,
                "m3_canary_profile_available": bool(viable),
                "canary_minimum_outcomes_configured": all(
                    isinstance((configs.get(f"{row['user_id']}:ml") or {}).get("canary_minimum_outcomes"), int)
                    and (configs.get(f"{row['user_id']}:ml") or {}).get("canary_minimum_outcomes") > 0
                    for row in active_profiles
                ),
                "canary_minimum_outcomes": next(
                    (
                        (configs.get(f"{row['user_id']}:ml") or {}).get("canary_minimum_outcomes")
                        for row in active_profiles
                        if (configs.get(f"{row['user_id']}:ml") or {}).get("canary_minimum_outcomes") is not None
                    ),
                    None,
                ),
            },
        }
        connection.rollback()
        return _json_value(report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_PUBLIC_URL or DATABASE_URL is required")
    with psycopg2.connect(database_url, connect_timeout=15) as connection:
        report = collect(connection)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if args.compact else 2,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if not args.quiet:
        print(payload)
    elif args.output:
        print(json.dumps({"output": str(args.output), "contract": report["contract"]}))


if __name__ == "__main__":
    main()
