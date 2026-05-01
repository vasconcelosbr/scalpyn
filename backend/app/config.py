import os
from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Scalpyn API"
    DATABASE_URL: str = "postgresql+asyncpg://scalpyn:scalpyn@localhost:5432/scalpyn"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "supersecret"
    ENCRYPTION_KEY: str = "0123456789abcdef0123456789abcdef"

    # ── Robust-indicator pipeline (Phase 1 — shadow mode) ────────────────────
    # When True, every pipeline scan also runs the new robust pipeline in
    # parallel with the legacy one and persists snapshots to
    # ``indicator_snapshots``. Legacy scoring stays authoritative regardless
    # of this flag.
    USE_ROBUST_INDICATORS: bool = False

    # ── Robust-indicator pipeline (Phase 3 — deprecation, robust default) ────
    # Phase 3 makes the robust engine the formal default everywhere. The
    # historic per-symbol bucket math (``int(sha1(symbol).hexdigest(),
    # 16) % 100 < percent``) is preserved as a diagnostic utility but is
    # no longer on the hot path: every score read returns the robust
    # engine result unless ``LEGACY_PIPELINE_ROLLBACK`` is set.
    USE_ROBUST_INDICATORS_PERCENT: int = 100

    # When True, the pre-flight safety guard for raising the rollout
    # percent is bypassed (still evaluates and reports unsafe reasons,
    # just doesn't block). Use only for emergency rollbacks.
    FORCE_ROLLOUT_RAISE: bool = False

    # ── Phase 3 emergency rollback ───────────────────────────────────────────
    # Single-flag rollback to the legacy ScoreEngine / futures_pipeline_scorer
    # path. When True, every authoritative-score read short-circuits to the
    # legacy result regardless of bucketing. This is the ONLY way to revive
    # the legacy engine in production. Default ``False`` — flipping it is
    # an emergency-only operation and triggers a daily standby alert if it
    # stays True for more than 24h (see ``app.tasks.robust_alerts``).
    LEGACY_PIPELINE_ROLLBACK: bool = False

    # Single ops-only Slack webhook for robust-indicator divergence /
    # alert notifications. When unset, alerts are logged at INFO and
    # dropped — they are NEVER broadcast to per-user webhooks (would
    # leak one tenant's symbol/score data into another tenant's Slack).
    ROBUST_ALERTS_OPS_WEBHOOK_URL: str = ""

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_db_url(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        v = v.replace("?sslmode=disable", "").replace("&sslmode=disable", "").replace("sslmode=disable&", "")
        return v

settings = Settings()
