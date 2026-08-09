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

_SYSTEMIC_OUTPUT = {
    "type": "object",
    "required": [
        "diagnosis", "root_cause_classification", "affected_modules", "evidence",
        "data_quality", "market_regime", "memory_hits", "recommendations",
        "warnings", "limitations",
    ],
    "properties": {
        "diagnosis": {"type": "string", "minLength": 1},
        "root_cause_classification": {"type": "string", "minLength": 1},
        "affected_modules": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        "discarded_hypotheses": {"type": "array", "items": {"type": "object"}},
        "data_quality": {"type": "object"},
        "market_regime": {"type": "object"},
        "memory_hits": {"type": "array", "items": {"type": "object"}},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "target_module", "target_path", "operation", "side_effect_class",
                    "confidence", "risk_conflicts", "strategy_conflicts",
                    "validation_plan", "rollback_plan",
                ],
                "properties": {
                    "target_module": {"type": "string"},
                    "target_entity_id": {"type": ["string", "null"]},
                    "target_path": {"type": "string"},
                    "current_value": {}, "proposed_value": {}, "operation": {"type": "string"},
                    "side_effect_class": {"enum": ["NONE", "PROPOSAL_WRITE", "CANDIDATE_WRITE", "SHADOW_WRITE"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "expected_impact": {"type": "object"},
                    "risk_conflicts": {"type": "array", "items": {"type": "object"}},
                    "strategy_conflicts": {"type": "array", "items": {"type": "object"}},
                    "validation_plan": {"type": "object"},
                    "rollback_plan": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


def _prompt(
    key: str, version: str, system: str, user: str, *,
    tools: tuple[str, ...] = (), output_schema: dict | None = None,
) -> PromptVersion:
    prompt = PromptVersion.create(
        id=uuid5(NAMESPACE_URL, f"scalpyn:prompt:{key}:{version}"),
        prompt_key=key,
        semantic_version=version,
        status="APPROVED",
        system_template=system,
        user_template=user,
        input_schema_json={"type": "object"},
        output_schema_json=output_schema or _OUTPUT,
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
    _prompt(
        "systemic-multimodule", "2.0.0",
        (
            "You are Scalpyn systemic analysis. Use only canonical typed-tool evidence. "
            "Never invent metrics, causal claims, authority, or missing values. "
            "Global Risk and Strategies are hard vetoes; ML, Social Score, and Market Regime are read-only."
        ),
        (
            "Question: {question}\nCanonical dataset, configuration, and typed-tool evidence: {dataset}\n"
            "Configuration bundle: {configuration}\nReturn only JSON matching the approved systemic schema."
        ),
        output_schema=_SYSTEMIC_OUTPUT,
    ),
)


def initial_prompt_registry() -> PromptRegistry:
    return PromptRegistry(INITIAL_PROMPTS)
