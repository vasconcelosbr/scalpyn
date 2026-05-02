import os
from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Scalpyn API"
    DATABASE_URL: str = "postgresql+asyncpg://scalpyn:scalpyn@localhost:5432/scalpyn"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "supersecret"
    ENCRYPTION_KEY: str = "0123456789abcdef0123456789abcdef"

    # Single ops-only Slack webhook for robust-indicator alert
    # notifications (staleness / low-confidence / rejection-rate). When
    # unset, alerts are logged at INFO and dropped — they are NEVER
    # broadcast to per-user webhooks (would leak one tenant's
    # symbol/score data into another tenant's Slack).
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
