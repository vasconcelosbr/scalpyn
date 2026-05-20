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

    # Maximum seconds a trade may remain open before the Trade Monitor closes
    # it with outcome = "timeout".  Override via the TRADE_MONITOR_TIMEOUT_SECONDS
    # environment variable.  Default is 24 hours (86 400 s).
    TRADE_MONITOR_TIMEOUT_SECONDS: int = 86_400

    # ── Dynamic exit signals via order flow (additive — TP/SL always wins) ──
    # Master switch — when False, the new _check_exit_signals path is a no-op
    # and the monitor keeps the legacy TP/SL/timeout-only behavior. Default
    # off so the change ships dark; flip to 1 in env after observation.
    TRADE_MONITOR_EXIT_FLOW_ENABLED: bool = False
    # Order-flow look-back window passed to get_order_flow_data().
    TRADE_MONITOR_EXIT_FLOW_WINDOW_SECONDS: int = 60
    # Maximum acceptable data age (seconds). Stale flow → skip the check.
    TRADE_MONITOR_EXIT_FLOW_MAX_AGE_SECONDS: int = 20
    # Bear threshold for taker_ratio. For LONG: ratio < threshold = sellers
    # dominating → exit. For SHORT: ratio > (1 - threshold) = buyers
    # dominating → exit (symmetric).
    TRADE_MONITOR_EXIT_TAKER_BEAR_THRESHOLD: float = 0.35
    # Volume exhaustion multiplier. The check is satisfied when the
    # adverse-side dominance fraction exceeds (1 - 1/threshold), i.e. with
    # the default 2.0 → dominance > 0.5 (one side has > 50% net flow).
    TRADE_MONITOR_EXIT_VOLUME_SPIKE_THRESHOLD: float = 2.0

    # ── Symmetric Exit Metrics (Task #315 / #316) ────────────────────────────
    # Master switch — when True, ``TradeMonitorService._close_trade`` and
    # ``shadow_trade_monitor._capture_exit_features`` chamam
    # ``app.services.exit_metrics.build_exit_snapshot`` antes de fechar.
    # Default False (dark rollout — runbook Fase B). Setar em worker-execution
    # APENAS (única réplica que roda essas tasks).
    ENABLE_EXIT_METRICS_CAPTURE: bool = False
    # Quando True, ``GET /api/trades/{id}`` e ``GET /api/shadow-trades/{id}``
    # devolvem ``entry_metrics``/``exit_metrics`` e a UI renderiza a
    # comparação Entry | Exit lado-a-lado. Default False.
    ENABLE_EXIT_METRICS_UI: bool = False
    # Reservadas para escopo futuro (multi-snapshot intra-trade no runbook).
    # Declaradas aqui na Fase A com default False, sem call-sites — evitam
    # drift quando o seguinte commit ligar essas features.
    ENABLE_DECISION_SNAPSHOTS: bool = False
    ENABLE_SIGNAL_TIMELINE: bool = False

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
