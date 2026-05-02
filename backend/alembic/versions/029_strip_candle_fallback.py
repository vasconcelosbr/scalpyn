"""Phase 4 cleanup — merge prior heads & strip removed config keys.

Revision ID: 029
Revises: 028, 028_robust_engine_tag
Create Date: 2026-05-01

Phase 4 of the Robust Indicators rollout removed:
  * the candle-derived approximation flag ``allow_candle_fallback`` from
    indicator configs (volume_delta + taker_ratio now return ``None``
    when the primary order-flow source is missing);
  * the dual-write scoring fields ``dual_write_mode`` and
    ``confidence_weighting`` from score configs (the engine writes the
    confidence-weighted score directly into ``alpha_scores``).

This migration is idempotent JSONB plumbing — it strips those keys from
``config_profiles.config`` for every row that still carries them.

It also acts as the merge point for the two parallel heads at revision
028 (``028`` adds dual-write columns; ``028_robust_engine_tag`` adds
``engine_tag`` columns) so subsequent migrations have a single ancestor.

We DO NOT drop the database columns added by 028 / 027 (``alpha_score_v2``,
``confidence_metrics``, ``scoring_version`` on ``alpha_scores``;
``divergence_bucket`` on ``indicator_snapshots``) — they remain as
forward-compatible nullable columns so the cleanup is fully reversible
without a destructive schema change.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "029"
down_revision = ("028", "028_robust_engine_tag")
branch_labels = None
depends_on = None


_REMOVED_INDICATOR_KEYS = ("allow_candle_fallback",)
_REMOVED_INDICATOR_SECTIONS = ("volume_delta", "taker_ratio")
_REMOVED_SCORE_KEYS = ("dual_write_mode", "confidence_weighting")


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))

    # ── Strip ``allow_candle_fallback`` from indicator configs ─────────
    for section in _REMOVED_INDICATOR_SECTIONS:
        for key in _REMOVED_INDICATOR_KEYS:
            op.execute(
                sa.text(
                    """
                    UPDATE config_profiles
                       SET config = jsonb_set(
                               config,
                               ARRAY[:section],
                               (config -> :section) - :key,
                               false
                           )
                     WHERE config_type = 'indicators'
                       AND config ? :section
                       AND (config -> :section) ? :key
                    """
                ).bindparams(section=section, key=key)
            )

    # ── Strip dual-write keys from score configs ───────────────────────
    for key in _REMOVED_SCORE_KEYS:
        op.execute(
            sa.text(
                """
                UPDATE config_profiles
                   SET config = config - :key
                 WHERE config_type = 'score'
                   AND config ? :key
                """
            ).bindparams(key=key)
        )


def downgrade() -> None:
    # The forward step is purely a JSONB cleanup — no schema rollback is
    # required and re-introducing dead config keys would be a regression.
    pass
