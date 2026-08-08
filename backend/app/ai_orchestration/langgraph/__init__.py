"""Optional LangGraph runtime over the canonical Scalpyn AI foundation.

Importing this package performs no database setup and makes no provider call.
"""

from .config import LangGraphSettings, get_langgraph_settings
from .graphs import build_graph
from .registry import graph_registry
from .state import ScalpynGraphState

__all__ = [
    "LangGraphSettings",
    "ScalpynGraphState",
    "build_graph",
    "get_langgraph_settings",
    "graph_registry",
]
