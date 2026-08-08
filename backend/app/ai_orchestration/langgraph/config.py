from __future__ import annotations

from dataclasses import dataclass
import os


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

    def require_runtime(self) -> None:
        if self.runtime != "langgraph" or not self.runtime_enabled:
            raise RuntimeError("LANGGRAPH_RUNTIME_DISABLED")
        if not self.strict_msgpack:
            raise RuntimeError("LANGGRAPH_STRICT_MSGPACK_REQUIRED")


def get_langgraph_settings() -> LangGraphSettings:
    runtime = os.getenv("AI_ORCHESTRATION_RUNTIME", "native").strip().lower()
    if runtime not in {"native", "langgraph"}:
        raise RuntimeError("AI_ORCHESTRATION_RUNTIME must be native or langgraph")
    schema = os.getenv("LANGGRAPH_CHECKPOINT_SCHEMA", "langgraph_runtime").strip()
    if schema != "langgraph_runtime":
        raise RuntimeError("LANGGRAPH_CHECKPOINT_SCHEMA must be langgraph_runtime")
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
    )
