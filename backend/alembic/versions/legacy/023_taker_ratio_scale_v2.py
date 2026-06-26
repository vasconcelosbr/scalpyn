"""Migrate taker_ratio thresholds + stale indicators_json to Buy/(Buy+Sell) scale

Revision ID: 023_taker_ratio_buy_pressure_scale
Revises: 022_apply_pending_021_ddl
Create Date: 2026-04-27

Task #82 — adopts the canonical "Buy Volume Ratio" definition for
``taker_ratio``:

    taker_ratio = taker_buy_volume / (taker_buy_volume + taker_sell_volume)
                  → range [0, 1], equilibrium = 0.5

Until this migration the value was persisted in two scales depending
on which collector wrote last:

  * ``feature_engine`` and ``market_data_service``  →  buy / (buy+sell)  ∈ [0, 1]
  * ``order_flow_service`` and ``layer_order_flow`` →  buy / sell        ∈ (0, 5] or absurd

The wrong scale produced absurd values (~3.28e11 for PENGU_USDT,
~8.98e9 for SUI) that downstream evaluators saw as "VALOR INVÁLIDO"
in the Rejected tab. The collectors are unified by the same change
that introduces this migration; this script handles the two
on-disk artifacts of the legacy scale:

  1. **Profile thresholds** (``profiles.config`` JSONB) — every
     condition with ``indicator == "taker_ratio"`` (in
     ``block_rules.blocks[].conditions[]``) or ``field == "taker_ratio"``
     (in ``filters.conditions[]``) is rewritten so that the user's
     intent (e.g. "reject when sellers dominate") survives the scale
     change. Conversion: ``new = old / (old + 1)``, which is the
     monotonic mapping ``buy/sell → buy/(buy+sell)``. Examples:

         < 1.04  →  < 0.5098    (sellers slightly dominant)
         > 1.20  →  > 0.5455
         > 1.50  →  > 0.6000
         > 2.00  →  > 0.6667

     Idempotency: a per-row marker ``_taker_ratio_scale_v2: true``
     is added to ``profiles.config``; rows that already carry it are
     left untouched. The leading underscore mirrors the convention used
     elsewhere in the codebase for "internal/operational" config keys
     that should not surface in the ProfileBuilder UI.

  2. **Stale ``indicators.indicators_json``** — every row whose
     persisted ``taker_ratio`` falls outside the new plausibility
     bound [0, 1] is set to ``null``. The Rejected tab (#76) then
     falls back to the stored trace, so the UI stops rendering
     "Current: 32800000…" for these symbols. Real (in-bound) values
     written before this migration are preserved.

Downgrade is a no-op: dividing the new bounded value by ``(1 - x)``
to recover the legacy scale is mathematically possible but requires
the same migration to be tracked, and there is no need to revert
the user's threshold values to a definition the codebase no longer
implements.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from alembic import op
import sqlalchemy as sa


revision = "023_taker_ratio_scale_v2"
down_revision = "022_apply_pending_021_ddl"
branch_labels = None
depends_on = None


# Operators whose comparison is preserved by the monotonic mapping
# f(x) = x / (x + 1). All ordered comparisons retain their direction
# because f is strictly increasing on [0, ∞). Equality is converted
# numerically (lossy at ~1e-6 but irrelevant in practice).
_CONVERTIBLE_OPERATORS = {"<", "<=", ">", ">=", "=", "==", "between"}

_SCALE_FLAG_KEY = "_taker_ratio_scale_v2"


def _convert_threshold(value: Any) -> Any:
    """Convert a buy/sell threshold to the buy/(buy+sell) scale.

    Returns the same value unchanged if it cannot be parsed as a
    finite non-negative float (NaN, strings that aren't numbers,
    booleans, lists, dicts not handled here, etc.). The Python
    ``bool`` is excluded explicitly because ``isinstance(True, int)``
    is True in Python.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        # ``between`` operator carries a [low, high] pair.
        return [_convert_threshold(v) for v in value]
    try:
        x = float(value)
    except (TypeError, ValueError):
        return value
    if x != x:  # NaN
        return value
    if x < 0:
        return value
    # f(x) = x / (x + 1). Always in [0, 1).
    new = x / (x + 1.0)
    # Round to 4 decimals so the UI shows "0.5098" rather than
    # "0.5098039215686274".
    return round(new, 4)


def _migrate_condition(cond: Dict[str, Any], key_name: str) -> bool:
    """Mutate a single condition dict; return True if anything changed.

    ``key_name`` is the field that holds the indicator identity:
    ``"indicator"`` for block rules, ``"field"`` for filters.

    Two threshold shapes are supported:

      * ``{"operator": ">", "value": 1.5}`` — single-value comparisons.
      * ``{"operator": "between", "min": 0.8, "max": 1.5}`` — range
        comparisons (the shape consumed by
        ``app.services.rule_engine`` for ``between``). Both bounds are
        converted independently because ``f(x) = x/(x+1)`` is strictly
        increasing, so ``min <= x <= max`` is preserved.
    """
    if not isinstance(cond, dict):
        return False
    if cond.get(key_name) != "taker_ratio":
        return False
    operator = str(cond.get("operator", "")).strip()
    if operator not in _CONVERTIBLE_OPERATORS:
        return False

    changed = False

    if "value" in cond:
        old_value = cond["value"]
        new_value = _convert_threshold(old_value)
        if new_value != old_value:
            cond["value"] = new_value
            changed = True

    # Range conditions encoded as separate min/max keys.
    for bound_key in ("min", "max"):
        if bound_key in cond:
            old_bound = cond[bound_key]
            new_bound = _convert_threshold(old_bound)
            if new_bound != old_bound:
                cond[bound_key] = new_bound
                changed = True

    return changed


def _migrate_block_rules(block_rules: Any) -> bool:
    """Walk ``config.block_rules.blocks[].conditions[]`` in place."""
    if not isinstance(block_rules, dict):
        return False
    blocks = block_rules.get("blocks")
    if not isinstance(blocks, list):
        return False
    changed = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        conditions = block.get("conditions")
        if not isinstance(conditions, list):
            continue
        for cond in conditions:
            if _migrate_condition(cond, "indicator"):
                changed = True
    return changed


def _migrate_filters(filters: Any) -> bool:
    """Walk ``config.filters.conditions[]`` in place."""
    if not isinstance(filters, dict):
        return False
    conditions = filters.get("conditions")
    if not isinstance(conditions, list):
        return False
    changed = False
    for cond in conditions:
        # Filters historically use ``field`` but the ProfileBuilder UI
        # also writes ``indicator`` in some legacy rows; check both.
        if _migrate_condition(cond, "field"):
            changed = True
        elif _migrate_condition(cond, "indicator"):
            changed = True
    return changed


def _migrate_profile_config(config: Any) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Return (new_config, changed) for a single profile row.

    A row is considered already-migrated when it carries the
    ``_taker_ratio_scale_v2`` marker, regardless of whether anything
    was actually rewritten — this keeps the migration idempotent
    even on profiles that never had a taker_ratio rule.
    """
    if not isinstance(config, dict):
        return None, False
    if config.get(_SCALE_FLAG_KEY) is True:
        return None, False

    changed_blocks = _migrate_block_rules(config.get("block_rules"))
    changed_filters = _migrate_filters(config.get("filters"))
    config[_SCALE_FLAG_KEY] = True
    return config, (changed_blocks or changed_filters)


def _has_table(bind, name: str) -> bool:
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Profile threshold migration ────────────────────────────────────────
    if _has_table(bind, "profiles"):
        rows = bind.execute(
            sa.text("SELECT id, config FROM profiles WHERE config IS NOT NULL")
        ).fetchall()

        migrated_profiles = 0
        rewritten_thresholds = 0
        for row in rows:
            pid = row[0]
            raw = row[1]
            if isinstance(raw, str):
                try:
                    cfg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
            elif isinstance(raw, dict):
                # SQLAlchemy already deserialised the JSONB.
                cfg = raw
            else:
                continue

            new_cfg, changed = _migrate_profile_config(cfg)
            if new_cfg is None:
                continue
            if changed:
                rewritten_thresholds += 1
            migrated_profiles += 1
            bind.execute(
                sa.text("UPDATE profiles SET config = CAST(:cfg AS JSONB) WHERE id = :pid"),
                {"cfg": json.dumps(new_cfg), "pid": pid},
            )

        # Surfaced in alembic logs for traceability.
        print(
            f"[023] taker_ratio scale migration: profiles_marked={migrated_profiles}, "
            f"thresholds_rewritten={rewritten_thresholds}"
        )

    # ── 2. Zero out stale indicators_json values ──────────────────────────────
    # Stale rows are rows whose persisted taker_ratio falls outside the
    # new plausibility bound [0, 1]. With the legacy buy/sell scale a
    # value above 1.0 was both common and meaningless under the new
    # definition; zeroing it makes the Rejected tab fall back to the
    # stored trace (#76) until the next scheduler tick refreshes it
    # with a real, in-bound value.
    if _has_table(bind, "indicators"):
        result = bind.execute(
            sa.text(
                """
                UPDATE indicators
                SET indicators_json = jsonb_set(
                    indicators_json,
                    '{taker_ratio}',
                    'null'::jsonb,
                    false
                )
                WHERE indicators_json ? 'taker_ratio'
                  AND indicators_json->'taker_ratio' <> 'null'::jsonb
                  AND jsonb_typeof(indicators_json->'taker_ratio') = 'number'
                  AND (
                        (indicators_json->>'taker_ratio')::float8 < 0
                     OR (indicators_json->>'taker_ratio')::float8 > 1
                  )
                """
            )
        )
        try:
            cleared = result.rowcount
        except Exception:
            cleared = -1
        print(f"[023] indicators_json stale taker_ratio cleared: rows={cleared}")


def downgrade() -> None:
    # Intentionally empty: see module docstring.
    pass
