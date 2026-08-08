"""Scalpyn systemic AI orchestration foundation.

All provider-backed features enter through :class:`AIOrchestrationService`.
The package is intentionally provider-framework agnostic and fail-closed.
"""

from .contracts import AIRequest, AIResult
from .context import TenantAIContext
from .orchestrator import AIOrchestrationService

__all__ = ["AIRequest", "AIResult", "TenantAIContext", "AIOrchestrationService"]
