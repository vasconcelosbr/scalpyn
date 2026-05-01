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

    # ── Robust-indicator pipeline (Phase 2 — gradual rollout) ────────────────
    # Percentage of symbols (0–100) bucketed into the robust pipeline as the
    # authoritative score source. Bucketing is deterministic per-symbol via
    # ``int(sha1(symbol).hexdigest(), 16) % 100 < percent``. The recommended
    # ramp is 10 → 50 → 100 with the pre-flight guard between each step.
    USE_ROBUST_INDICATORS_PERCENT: int = 0

    # When True, the pre-flight safety guard for raising the rollout
    # percent is bypassed (still evaluates and reports unsafe reasons,
    # just doesn't block). Use only for emergency rollbacks.
    FORCE_ROLLOUT_RAISE: bool = False

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
