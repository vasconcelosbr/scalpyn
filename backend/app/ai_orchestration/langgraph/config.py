from __future__ import annotations

from dataclasses import dataclass
import os
from types import MappingProxyType
from typing import Mapping


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class LangGraphSettings:
    runtime: str
    runtime_enabled: bool
    entrypoints_enabled: bool
    regenerative_shadow_enabled: bool
    real_provider_canary_enabled: bool
    strict_msgpack: bool
    checkpoint_schema: str
    node_timeout_seconds: int
    lease_seconds: int
    model_approval_ttl_seconds: int
    tool_default_max_rows: int
    module_flags: Mapping[str, bool]

    def require_runtime(self) -> None:
        if self.runtime != "langgraph" or not self.runtime_enabled:
            raise RuntimeError("LANGGRAPH_RUNTIME_DISABLED")
        if not self.strict_msgpack:
            raise RuntimeError("LANGGRAPH_STRICT_MSGPACK_REQUIRED")

    def require_module(self, module_key: str) -> None:
        if not self.module_flags.get(module_key, False):
            raise RuntimeError(f"AI_MODULE_DISABLED:{module_key}")


def get_langgraph_settings() -> LangGraphSettings:
    runtime = os.getenv("AI_ORCHESTRATION_RUNTIME", "native").strip().lower()
    if runtime not in {"native", "langgraph"}:
        raise RuntimeError("AI_ORCHESTRATION_RUNTIME must be native or langgraph")
    schema = os.getenv("LANGGRAPH_CHECKPOINT_SCHEMA", "langgraph_runtime").strip()
    if schema != "langgraph_runtime":
        raise RuntimeError("LANGGRAPH_CHECKPOINT_SCHEMA must be langgraph_runtime")
    module_env = {
        "strategy_profiles": "AI_MODULE_STRATEGY_PROFILES_ENABLED",
        "ml_models": "AI_MODULE_ML_MODELS_ENABLED",
        "shadow_portfolio": "AI_MODULE_SHADOW_ENABLED",
        "score_engine": "AI_MODULE_SCORE_ENGINE_ENABLED",
        "global_risk": "AI_MODULE_GLOBAL_RISK_ENABLED",
        "strategies": "AI_MODULE_STRATEGIES_ENABLED",
        "social_score": "AI_MODULE_SOCIAL_SCORE_ENABLED",
        "market_regime": "AI_MODULE_MARKET_REGIME_ENABLED",
        "intelligence_runs": "AI_MODULE_INTELLIGENCE_RUNS_ENABLED",
        "audit_version_memory": "AI_MODULE_AUDIT_MEMORY_ENABLED",
    }
    return LangGraphSettings(
        runtime=runtime,
        runtime_enabled=_flag("LANGGRAPH_RUNTIME_ENABLED"),
        entrypoints_enabled=_flag("LANGGRAPH_ENTRYPOINTS_ENABLED"),
        regenerative_shadow_enabled=_flag("LANGGRAPH_REGENERATIVE_SHADOW_ENABLED"),
        real_provider_canary_enabled=_flag("LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED"),
        strict_msgpack=_flag("LANGGRAPH_STRICT_MSGPACK"),
        checkpoint_schema=schema,
        node_timeout_seconds=max(1, int(os.getenv("LANGGRAPH_NODE_TIMEOUT_SECONDS", "120"))),
        lease_seconds=max(30, int(os.getenv("LANGGRAPH_LEASE_SECONDS", "300"))),
        model_approval_ttl_seconds=max(
            60, int(os.getenv("AI_MODEL_APPROVAL_TTL_SECONDS", "900")),
        ),
        tool_default_max_rows=max(1, int(os.getenv("AI_TOOL_DEFAULT_MAX_ROWS", "20"))),
        module_flags=MappingProxyType({key: _flag(env_name) for key, env_name in module_env.items()}),
    )
