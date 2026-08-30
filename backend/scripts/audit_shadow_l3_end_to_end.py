"""Full-history, read-only audit of Decision -> L3 v3 -> outbox -> Shadow.

The collector walks ``decisions_log`` by primary-key blocks so production does
not need a large JSONB sort or a disk-backed temporary relation.  It emits only
aggregates and immutable identifiers; database URLs are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import text

if os.environ.get("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

from app.database import CeleryAsyncSessionLocal
from app.services.l3_authorization_contract_v3 import (
    contract_authorizes_shadow_capture,
)
from app.services.profile_runtime_config import canonical_profile_config_hash


L3_CONTRACT_KEY = "l3_authorization_contract_v3"
L3_GATE_KEY = "l3_gate_v2"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _day(value: datetime) -> str:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).date().isoformat()


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    rows = [float(value) for value in values]
    return {
        "n": len(rows),
        "min_seconds": min(rows) if rows else None,
        "p50_seconds": _percentile(rows, 0.50),
        "p95_seconds": _percentile(rows, 0.95),
        "max_seconds": max(rows) if rows else None,
    }


def _walk_reason_codes(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"reason_code", "code"} and isinstance(child, str):
                yield child
            elif key == "reason_codes" and isinstance(child, (list, tuple)):
                for item in child:
                    if isinstance(item, str):
                        yield item
            yield from _walk_reason_codes(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_reason_codes(child)


def _conditions(config: Mapping[str, Any] | None) -> Iterable[tuple[str, dict]]:
    source = dict(config or {})
    for section in ("filters", "signals", "entry_triggers"):
        for condition in ((source.get(section) or {}).get("conditions") or []):
            if isinstance(condition, Mapping):
                yield section, dict(condition)
    for block in ((source.get("block_rules") or {}).get("blocks") or []):
        if not isinstance(block, Mapping):
            continue
        conditions = block.get("conditions")
        if conditions is None:
            conditions = [block]
        for condition in conditions or []:
            if isinstance(condition, Mapping):
                yield "block_rules", dict(condition)


def _technical_approved(row: Mapping[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    gate = metrics.get(L3_GATE_KEY) or {}
    explicit = gate.get("shadow_decision") or gate.get("decision")
    if explicit is not None:
        return str(explicit).upper() == "ALLOW"
    return (
        str(row.get("decision") or "").upper() == "ALLOW"
        and row.get("l1_pass") is True
        and row.get("l2_pass") is True
        and row.get("l3_pass") is True
    )


def _outbox_result(events: list[Mapping[str, Any]]) -> str | None:
    results = [
        str((event.get("payload") or {}).get("processing_result"))
        for event in events
        if (event.get("payload") or {}).get("processing_result")
    ]
    return results[-1] if results else None


def _terminal_state(
    row: Mapping[str, Any],
    *,
    events: list[Mapping[str, Any]],
    has_shadow: bool,
) -> str:
    if not _technical_approved(row):
        return "BLOCK"
    result = _outbox_result(events) or ""
    if result.startswith("SUPPRESSED/"):
        return "SUPPRESSED"
    if has_shadow:
        return "ALLOW"
    contract = (row.get("metrics") or {}).get(L3_CONTRACT_KEY) or {}
    if contract.get("authorization_status") == "CONTRACT_REJECT":
        return "CONTRACT_REJECT"
    return "ALLOW"


def _primary_no_shadow_cause(
    row: Mapping[str, Any], events: list[Mapping[str, Any]]
) -> str:
    if not _technical_approved(row):
        return "L3_TECHNICAL_BLOCK"
    result = _outbox_result(events)
    if result and result.startswith("SUPPRESSED/"):
        return result
    contract = (row.get("metrics") or {}).get(L3_CONTRACT_KEY) or {}
    if contract.get("authorization_status") == "CONTRACT_REJECT":
        return "CONTRACT_REJECT"
    if not events:
        return "OUTBOX_MISSING"
    statuses = {str(event.get("status") or "UNKNOWN") for event in events}
    if statuses & {"PENDING", "RETRY"}:
        return "OUTBOX_" + "+".join(sorted(statuses & {"PENDING", "RETRY"}))
    if result:
        return result
    return "APPROVED_WITHOUT_SHADOW_OR_TERMINAL_RESULT"


async def collect(*, block_size: int, temp_file_limit: str) -> dict[str, Any]:
    async with CeleryAsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(text("SET LOCAL statement_timeout = '120s'"))
        await db.execute(
            text("SELECT set_config('temp_file_limit', :limit, true)"),
            {"limit": temp_file_limit},
        )

        bounds = (await db.execute(text("""
            SELECT COUNT(*) AS row_count, MIN(id) AS min_id, MAX(id) AS max_id,
                   MIN(created_at) AS first_created_at,
                   MAX(created_at) AS last_created_at
              FROM decisions_log
             WHERE metrics ? 'l3_gate_v2'
        """))).mappings().one()

        outbox_rows = list((await db.execute(text("""
            SELECT id, decision_id, event_type, status, attempt_count,
                   last_error, payload, created_at, processed_at
              FROM l3_authorization_outbox
             ORDER BY decision_id, created_at, id
        """))).mappings().all())
        outbox_by_decision: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in outbox_rows:
            outbox_by_decision[int(row["decision_id"])].append(row)

        shadow_rows = list((await db.execute(text("""
            SELECT id, decision_id, created_at, status, outcome, symbol,
                   profile_id, profile_name, source, strategy_type
              FROM shadow_trades
             WHERE strategy_type = 'PROFILE_L3'
               AND source = 'L3'
             ORDER BY created_at, id
        """))).mappings().all())
        shadow_by_decision: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        shadows_by_day: Counter[str] = Counter()
        for row in shadow_rows:
            shadows_by_day[_day(row["created_at"])] += 1
            if row.get("decision_id") is not None:
                shadow_by_decision[int(row["decision_id"])].append(row)

        profile_rows = list((await db.execute(text("""
            SELECT DISTINCT ON (p.id)
                   p.id AS profile_id, p.name AS profile_name, p.config,
                   p.profile_version, pv.id AS profile_version_id,
                   pv.config_hash AS version_config_hash
              FROM profiles p
              JOIN pipeline_watchlists w
                ON w.profile_id = p.id
               AND upper(w.level) = 'L3'
               AND w.auto_refresh IS TRUE
              LEFT JOIN LATERAL (
                  SELECT id, config_hash
                    FROM profile_versions
                   WHERE profile_id = p.id
                     AND is_active IS TRUE
                     AND status = 'CHAMPION'
                   ORDER BY version_number DESC, created_at DESC
                   LIMIT 1
              ) pv ON TRUE
             WHERE p.is_active IS TRUE
             ORDER BY p.id
        """))).mappings().all())

        daily: dict[str, Counter[str]] = defaultdict(Counter)
        no_shadow_primary: Counter[str] = Counter()
        no_shadow_periods: dict[str, Counter[str]] = defaultdict(Counter)
        contract_reasons: Counter[str] = Counter()
        contract_reason_periods: dict[str, Counter[str]] = defaultdict(Counter)
        all_reason_codes: Counter[str] = Counter()
        operator_unsupported: Counter[str] = Counter()
        unsupported_profiles: dict[str, set[str]] = defaultdict(set)
        operator_unsupported_samples: list[dict[str, Any]] = []
        approved_contract_reject_samples: list[dict[str, Any]] = []
        shadow_mode_unlockable_contract_rejects = 0
        producers: Counter[tuple[str, str, str]] = Counter()
        freshness: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        outcome_rows: list[dict[str, Any]] = []
        decision_count = 0
        no_shadow_count = 0
        approved_count = 0
        approved_without_shadow = 0
        first_v3: dict[str, Any] | None = None
        last_v3: dict[str, Any] | None = None

        last_id = int(bounds.get("min_id") or 0) - 1
        max_id = int(bounds.get("max_id") or -1)
        while last_id < max_id:
            rows = list((await db.execute(text("""
                SELECT id, symbol, strategy, decision, l1_pass, l2_pass,
                       l3_pass, reasons, metrics, direction, event_type,
                       profile_id, profile_name, score_status, gate_action,
                       reason_codes, created_at
                  FROM decisions_log
                 WHERE id > :last_id
                   AND id <= :max_id
                   AND metrics ? 'l3_gate_v2'
                 ORDER BY id
                 LIMIT :block_size
            """), {
                "last_id": last_id,
                "max_id": max_id,
                "block_size": block_size,
            })).mappings().all())
            if not rows:
                break
            for row in rows:
                decision_count += 1
                decision_id = int(row["id"])
                last_id = decision_id
                day = _day(row["created_at"])
                events = outbox_by_decision.get(decision_id, [])
                has_shadow = bool(shadow_by_decision.get(decision_id))
                approved = _technical_approved(row)
                if approved:
                    approved_count += 1
                    daily[day]["technical_approved"] += 1
                    if not has_shadow:
                        approved_without_shadow += 1
                if has_shadow:
                    daily[day]["shadow_linked"] += 1
                state = _terminal_state(row, events=events, has_shadow=has_shadow)
                daily[day][state] += 1
                metrics = row.get("metrics") or {}
                contract = metrics.get(L3_CONTRACT_KEY) or {}
                if contract:
                    marker = {
                        "id": decision_id,
                        "created_at": _iso(row["created_at"]),
                        "authorization_status": contract.get("authorization_status"),
                    }
                    if first_v3 is None:
                        first_v3 = marker
                    last_v3 = marker
                    for code in contract.get("reason_codes") or []:
                        contract_reasons[str(code)] += 1
                        contract_reason_periods[str(code)][day] += 1
                    evaluated_at = _parse_time(contract.get("evaluated_at"))
                    for candidate in contract.get("feature_registry") or []:
                        if not isinstance(candidate, Mapping):
                            continue
                        source = str(candidate.get("source") or "NONE")
                        provider = str(candidate.get("source_provider") or "NONE")
                        timeframe = str(candidate.get("timeframe") or "NONE")
                        key = (source, provider, timeframe)
                        producers[key] += 1
                        source_at = _parse_time(candidate.get("source_timestamp"))
                        if evaluated_at and source_at:
                            freshness[key].append(
                                (evaluated_at - source_at).total_seconds()
                            )
                    for evaluation in contract.get("feature_evaluations") or []:
                        if not isinstance(evaluation, Mapping):
                            continue
                        if "OPERATOR_UNSUPPORTED" in (
                            evaluation.get("reason_codes") or []
                        ):
                            operator = str(evaluation.get("operator") or "NONE")
                            operator_unsupported[operator] += 1
                            if row.get("profile_id"):
                                unsupported_profiles[operator].add(
                                    str(row["profile_id"])
                                )
                            if len(operator_unsupported_samples) < 25:
                                operator_unsupported_samples.append({
                                    "decision_id": decision_id,
                                    "created_at": _iso(row["created_at"]),
                                    "profile_id": str(row.get("profile_id") or ""),
                                    "profile_name": row.get("profile_name"),
                                    "section": evaluation.get("section"),
                                    "condition_id": evaluation.get("condition_id"),
                                    "indicator": evaluation.get("indicator"),
                                    "operator": evaluation.get("operator"),
                                    "feature_identity": evaluation.get("feature_identity"),
                                    "reason_codes": evaluation.get("reason_codes") or [],
                                })
                    if (
                        approved
                        and contract.get("authorization_status") == "CONTRACT_REJECT"
                    ):
                        capture_authorized = contract_authorizes_shadow_capture(
                            contract,
                            legacy_decision=row.get("decision"),
                        )
                        shadow_mode_unlockable_contract_rejects += int(
                            capture_authorized
                        )
                        if len(approved_contract_reject_samples) < 25:
                            approved_contract_reject_samples.append({
                                "decision_id": decision_id,
                                "created_at": _iso(row["created_at"]),
                                "symbol": row.get("symbol"),
                                "profile_id": str(row.get("profile_id") or ""),
                                "profile_name": row.get("profile_name"),
                                "contract_reason_codes": contract.get("reason_codes") or [],
                                "provenance_resolution": contract.get(
                                    "provenance_resolution"
                                ),
                                "outbox_result": _outbox_result(events),
                                "capture_authorized_by_current_code": (
                                    capture_authorized
                                ),
                            })
                for code in set(_walk_reason_codes({
                    "reasons": row.get("reasons"),
                    "reason_codes": row.get("reason_codes"),
                    "metrics": metrics,
                })):
                    all_reason_codes[code] += 1
                if not has_shadow:
                    no_shadow_count += 1
                    cause = _primary_no_shadow_cause(row, events)
                    no_shadow_primary[cause] += 1
                    no_shadow_periods[cause][day] += 1
            if len(rows) < block_size:
                break

        profile_summary = []
        active_operator_profiles: dict[str, set[str]] = defaultdict(set)
        active_source_indicators: dict[str, set[str]] = defaultdict(set)
        for row in profile_rows:
            config = row.get("config") or {}
            for _section, condition in _conditions(config):
                operator = str(condition.get("operator") or "NONE")
                active_operator_profiles[operator].add(str(row["profile_id"]))
                indicator = str(
                    condition.get("indicator") or condition.get("field") or "NONE"
                )
                if indicator in {
                    "orderbook_pressure", "bid_ask_imbalance",
                    "orderbook_depth_usdt", "spread_pct", "spread",
                }:
                    active_source_indicators["live_order_book"].add(indicator)
            computed_hash = canonical_profile_config_hash(config)
            profile_summary.append({
                "profile_id": str(row["profile_id"]),
                "profile_name": row["profile_name"],
                "profile_version": _iso(row.get("profile_version")),
                "profile_version_id": (
                    str(row["profile_version_id"])
                    if row.get("profile_version_id") else None
                ),
                "profile_config_hash": computed_hash,
                "version_config_hash": row.get("version_config_hash"),
                "version_hash_match": computed_hash == row.get("version_config_hash"),
            })

        daily_rows = []
        for day in sorted(set(daily) | set(shadows_by_day)):
            row = daily[day]
            approved = int(row.get("technical_approved", 0))
            created = int(shadows_by_day.get(day, 0))
            daily_rows.append({
                "day_utc": day,
                "ALLOW": int(row.get("ALLOW", 0)),
                "SUPPRESSED": int(row.get("SUPPRESSED", 0)),
                "BLOCK": int(row.get("BLOCK", 0)),
                "CONTRACT_REJECT": int(row.get("CONTRACT_REJECT", 0)),
                "technical_approved": approved,
                "canonical_shadows_created": created,
                "canonical_shadows_linked_to_decision": int(row.get("shadow_linked", 0)),
                "shadow_capture_ratio": (created / approved) if approved else None,
            })

        def ranked(counter: Counter[str], periods: Mapping[str, Counter[str]]) -> list[dict]:
            return [
                {
                    "cause": cause,
                    "count": count,
                    "pct_of_no_shadow_decisions": (
                        count / no_shadow_count * 100 if no_shadow_count else None
                    ),
                    "dominant_day_utc": (
                        periods[cause].most_common(1)[0][0]
                        if periods.get(cause) else None
                    ),
                    "dominant_day_count": (
                        periods[cause].most_common(1)[0][1]
                        if periods.get(cause) else 0
                    ),
                }
                for cause, count in counter.most_common()
            ]

        await db.rollback()

    return {
        "contract": "shadow_l3_full_history_audit_v1",
        "generated_at": _iso(datetime.now(timezone.utc)),
        "execution": {
            "read_only": True,
            "block_size": block_size,
            "temp_file_limit": temp_file_limit,
        },
        "decision_scope": {
            "predicate": "decisions_log.metrics ? 'l3_gate_v2'",
            "row_count_query": int(bounds.get("row_count") or 0),
            "row_count_processed": decision_count,
            "min_id": bounds.get("min_id"),
            "max_id": bounds.get("max_id"),
            "first_created_at": _iso(bounds.get("first_created_at")),
            "last_created_at": _iso(bounds.get("last_created_at")),
        },
        "canonical_shadow_scope": {
            "predicate": "strategy_type='PROFILE_L3' AND source='L3'",
            "row_count": len(shadow_rows),
            "with_decision_id": sum(row.get("decision_id") is not None for row in shadow_rows),
        },
        "outbox_scope": {
            "row_count": len(outbox_rows),
            "status_counts": dict(Counter(str(row["status"]) for row in outbox_rows)),
            "processing_result_counts": dict(Counter(
                str((row.get("payload") or {}).get("processing_result") or "NONE")
                for row in outbox_rows
            )),
            "last_error_counts": dict(Counter(
                str(row.get("last_error")) for row in outbox_rows if row.get("last_error")
            )),
        },
        "totals": {
            "technical_approved": approved_count,
            "no_shadow": no_shadow_count,
            "approved_without_canonical_shadow": approved_without_shadow,
            "shadow_mode_unlockable_contract_rejects": (
                shadow_mode_unlockable_contract_rejects
            ),
            "canonical_shadows": len(shadow_rows),
        },
        "daily": daily_rows,
        "first_v3_decision": first_v3,
        "last_v3_decision": last_v3,
        "ranked_primary_no_shadow_causes": ranked(no_shadow_primary, no_shadow_periods),
        "contract_reason_code_occurrences": ranked(
            contract_reasons, contract_reason_periods
        ),
        "all_reason_code_decision_occurrences": dict(all_reason_codes.most_common()),
        "operator_unsupported": [
            {
                "operator": operator,
                "decision_count": count,
                "historical_profile_count": len(unsupported_profiles[operator]),
                "active_profile_count_using_operator": len(
                    active_operator_profiles.get(operator, set())
                ),
            }
            for operator, count in operator_unsupported.most_common()
        ],
        "operator_unsupported_samples": operator_unsupported_samples,
        "approved_contract_reject_samples": approved_contract_reject_samples,
        "active_profile_operators": {
            operator: len(profile_ids)
            for operator, profile_ids in sorted(active_operator_profiles.items())
        },
        "active_order_book_indicators": sorted(
            active_source_indicators.get("live_order_book", set())
        ),
        "active_profiles": profile_summary,
        "producer_candidate_counts": [
            {
                "source": key[0], "provider": key[1], "timeframe": key[2],
                "count": count,
            }
            for key, count in sorted(producers.items())
        ],
        "freshness_by_source_provider_timeframe": [
            {
                "source": key[0], "provider": key[1], "timeframe": key[2],
                **_distribution(values),
            }
            for key, values in sorted(freshness.items())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block-size", type=int, default=1000)
    parser.add_argument("--temp-file-limit", default="64MB")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if args.block_size < 1 or args.block_size > 5000:
        raise SystemExit("--block-size must be between 1 and 5000")
    print(json.dumps(
        asyncio.run(collect(
            block_size=args.block_size,
            temp_file_limit=args.temp_file_limit,
        )),
        ensure_ascii=False,
        sort_keys=True,
        indent=None if args.compact else 2,
        default=str,
    ))


if __name__ == "__main__":
    main()
