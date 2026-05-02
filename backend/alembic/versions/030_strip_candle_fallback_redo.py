"""Re-run 029's JSONB cleanup deterministically (Task #158 follow-on).

Revision ID: 030
Revises: 029
Create Date: 2026-05-02

Migration ``029_strip_candle_fallback`` originally referenced the wrong
column (``config`` instead of ``config_json`` — see Task #158). On the
failed Cloud Run deploy the JSONB ``UPDATE`` aborted with
``column "config" does not exist``, ``alembic upgrade head`` exited 1,
and ``backend/start.sh``'s stamp-head fallback wrote ``029`` to
``alembic_version`` *without* the data changes ever applying.

Fixing 029 in place is necessary but not sufficient: alembic will not
re-run a revision that is already stamped. So this revision repeats the
same idempotent JSONB cleanup with the correct column name. It is a
no-op on any database that already had 029 apply cleanly (the ``WHERE
config_json ? :section`` predicates filter out rows without the keys).

Removed by Phase 4 of the Robust Indicators rollout:
  * ``allow_candle_fallback`` from indicator configs (volume_delta and
    taker_ratio sections);
  * ``dual_write_mode`` and ``confidence_weighting`` from score configs.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "030"
down_revision = "029"
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
                       SET config_json = jsonb_set(
                               config_json,
                               ARRAY[:section],
                               (config_json -> :section) - :key,
                               false
                           )
                     WHERE config_type = 'indicators'
                       AND config_json ? :section
                       AND (config_json -> :section) ? :key
                    """
                ).bindparams(section=section, key=key)
            )

    # ── Strip dual-write keys from score configs ───────────────────────
    for key in _REMOVED_SCORE_KEYS:
        op.execute(
            sa.text(
                """
                UPDATE config_profiles
                   SET config_json = config_json - :key
                 WHERE config_type = 'score'
                   AND config_json ? :key
                """
            ).bindparams(key=key)
        )


def downgrade() -> None:
    # The forward step is purely a JSONB cleanup — no schema rollback is
    # required and re-introducing dead config keys would be a regression.
    pass
