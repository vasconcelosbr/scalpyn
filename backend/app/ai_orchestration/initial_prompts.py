from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from .prompt_registry import PromptRegistry, PromptVersion


_OUTPUT = {
    "type": "object",
    "required": ["analysis", "recommendations"],
    "properties": {
        "analysis": {"type": "object"},
        "recommendations": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


def _prompt(key: str, version: str, system: str, user: str, *, tools: tuple[str, ...] = ()) -> PromptVersion:
    prompt = PromptVersion.create(
        id=uuid5(NAMESPACE_URL, f"scalpyn:prompt:{key}:{version}"),
        prompt_key=key,
        semantic_version=version,
        status="APPROVED",
        system_template=system,
        user_template=user,
        input_schema_json={"type": "object"},
        output_schema_json=_OUTPUT,
        tool_policy_json={"allowlist": list(tools), "live_write": False},
        provider_constraints_json={"required_capabilities": ["text", "structured_output"]},
    )
    return prompt.model_copy(update={"approved_at": prompt.created_at})


INITIAL_PROMPTS = (
    _prompt(
        "profile-suggestion-explanation", "1.0.0",
        "You explain Scalpyn profile suggestions using only supplied evidence. Never invent metrics.",
        "Question: {question}\nSuggestion evidence: {evidence}\nReturn the approved JSON schema.",
    ),
    _prompt(
        "shadow-detailed-analysis", "1.0.0",
        "You audit tenant-scoped Shadow trades. Association is not causation. Cite trade IDs.",
        "Question: {question}\nFrozen dataset: {dataset}\nConfiguration: {configuration}\nReturn the approved JSON schema.",
    ),
    _prompt(
        "ai-critic", "1.0.0",
        "You are the analysis-only Scalpyn AI Critic. You have no mutation or live authority.",
        "Question: {question}\nCanonical context: {dataset}\nReturn the approved JSON schema.",
    ),
    _prompt(
        "copilot", "1.0.0",
        "You are Scalpyn Co-Pilot. Tool policy is enforced by code; live writes are denied.",
        "Question: {question}\nScreen context: {context}\nReturn the approved JSON schema.",
        tools=("shadow.get_performance_summary", "profiles.get_effective_configuration", "audit.get_change_lineage"),
    ),
)


def initial_prompt_registry() -> PromptRegistry:
    return PromptRegistry(INITIAL_PROMPTS)
