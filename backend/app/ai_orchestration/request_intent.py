"""Explicit, fail-closed intent resolution for provider execution."""

from __future__ import annotations

from typing import Any

from .contracts import AIRequestIntent
from .errors import ProviderBlockedError


_FAKE_MARKERS = ("staging_canary", "fake_provider")
_REAL_MARKERS = ("provider_canary", "real_provider_canary")


def _any_enabled(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(payload.get(key) is True for key in keys)


def _all_enabled(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return all(payload.get(key) is True for key in keys)


def resolve_request_intent(request_json: dict[str, Any]) -> AIRequestIntent:
    """Resolve persisted intent, preserving unambiguous legacy semantics only."""

    raw_intent = request_json.get("request_intent")
    fake_marker_present = _any_enabled(request_json, _FAKE_MARKERS)
    fake_markers_complete = _all_enabled(request_json, _FAKE_MARKERS)
    real_marked = _any_enabled(request_json, _REAL_MARKERS)

    if raw_intent is None:
        if fake_markers_complete and not real_marked:
            return AIRequestIntent.FAKE_PROVIDER_CANARY
        if not fake_marker_present and not real_marked:
            return AIRequestIntent.NORMAL_ANALYSIS
        raise ProviderBlockedError(
            "REQUEST_INTENT_MARKERS_AMBIGUOUS",
            "Request intent markers are ambiguous; provider transport was not attempted",
        )

    try:
        intent = AIRequestIntent(str(raw_intent))
    except ValueError as exc:
        raise ProviderBlockedError(
            "REQUEST_INTENT_INVALID",
            "Request intent is invalid; provider transport was not attempted",
        ) from exc

    if intent is AIRequestIntent.NORMAL_ANALYSIS and (fake_marker_present or real_marked):
        raise ProviderBlockedError(
            "REQUEST_INTENT_MARKERS_AMBIGUOUS",
            "Normal analysis cannot carry canary markers",
        )
    if intent is AIRequestIntent.FAKE_PROVIDER_CANARY:
        if not fake_markers_complete or real_marked:
            raise ProviderBlockedError(
                "FAKE_PROVIDER_CANARY_MARKERS_INVALID",
                "Fake provider canary markers are incomplete or contradictory",
            )
    if intent is AIRequestIntent.REAL_PROVIDER_CANARY:
        authorization = request_json.get("server_authorization")
        if (
            not real_marked
            or fake_marker_present
            or not isinstance(authorization, dict)
            or authorization.get("scope") != "REAL_PROVIDER_CANARY"
            or not str(authorization.get("authorization_id") or "").strip()
        ):
            raise ProviderBlockedError(
                "REAL_PROVIDER_CANARY_AUTHORIZATION_REQUIRED",
                "Real provider canary requires separate server-side authorization",
            )
    return intent


def validate_provider_intent_gate(
    intent: AIRequestIntent,
    *,
    environment_name: str,
    fake_provider_canary_enabled: bool,
    real_provider_canary_enabled: bool,
    normal_analysis_provider_enabled: bool,
) -> None:
    """Apply only the operational gate belonging to the resolved intent."""

    if intent is AIRequestIntent.NORMAL_ANALYSIS:
        if not normal_analysis_provider_enabled:
            raise ProviderBlockedError(
                "NORMAL_PROVIDER_DISABLED",
                "The governed provider gate for normal analysis is disabled",
            )
        return
    if intent is AIRequestIntent.FAKE_PROVIDER_CANARY:
        if "staging" not in environment_name.lower():
            raise ProviderBlockedError(
                "FAKE_PROVIDER_CANARY_STAGING_ONLY",
                "Fake provider canary is restricted to staging",
            )
        if not fake_provider_canary_enabled:
            raise ProviderBlockedError(
                "FAKE_PROVIDER_CANARY_DISABLED",
                "Fake provider canary is disabled",
            )
        if real_provider_canary_enabled:
            raise ProviderBlockedError(
                "FAKE_PROVIDER_CANARY_REAL_GATE_CONFLICT",
                "Fake provider canary requires the real canary gate to remain disabled",
            )
        return
    if intent is AIRequestIntent.REAL_PROVIDER_CANARY and not real_provider_canary_enabled:
        raise ProviderBlockedError(
            "REAL_PROVIDER_CANARY_DISABLED",
            "Real provider canary is disabled",
        )
