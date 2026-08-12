from __future__ import annotations

from typing import Annotated, Any, TypedDict


def append_unique(current: list, incoming: list | None) -> list:
    result = list(current or [])
    for item in incoming or []:
        if item not in result:
            result.append(item)
    return result


class ScalpynGraphState(TypedDict, total=False):
    state_schema_version: str
    ai_request_id: str
    tenant_id: str
    user_id: str | None
    graph_run_id: str
    graph_key: str
    graph_version: str
    job_id: str | None
    status: str
    authority: str
    current_node: str | None
    completed_nodes: Annotated[list[str], append_unique]
    event_keys: Annotated[list[str], append_unique]

    prompt_version_id: str | None
    prompt_hash: str | None
    model_resolution_id: str | None
    configured_model: str | None
    effective_model: str | None
    dataset_snapshot_id: str | None
    configuration_bundle_id: str | None

    tool_plan: list[dict[str, Any]]
    tool_call_ids: Annotated[list[str], append_unique]
    evidence_refs: Annotated[list[dict[str, Any]], append_unique]

    hypothesis_id: str | None
    change_set_id: str | None
    candidate_version_ids: Annotated[list[str], append_unique]
    experiment_id: str | None
    decision_memory_ids: Annotated[list[str], append_unique]
    memory_hits: Annotated[list[dict[str, Any]], append_unique]

    root_cause_classification: str | None
    recommendations: Annotated[list[dict[str, Any]], append_unique]
    warnings: Annotated[list[str], append_unique]
    limitations: Annotated[list[str], append_unique]

    pending_interrupt: dict[str, Any] | None
    interrupt_decision: dict[str, Any] | None
    result_json: dict[str, Any] | None
    terminal_reason: str | None

    conversation_id: str | None
    message_id: str | None
    parent_analysis_run_id: str | None
    parent_result_id: str | None
    parent_result_summary: str | None
    request_intent: str | None
    request_kind: str | None
    data_mode: str | None
    question: str | None
    conversation_summary: str | None
    recent_messages: list[dict[str, Any]]
    selected_evidence_refs: Annotated[list[dict[str, Any]], append_unique]
    readonly_tool_call_ids: Annotated[list[str], append_unique]
    new_data_snapshot_refs: Annotated[list[dict[str, Any]], append_unique]
    new_data_queried: bool
    child_analysis_run_id: str | None
    proposal_id: str | None
    proposal_execution_result: dict[str, Any] | None
    answer: dict[str, Any] | None
    reason_code: str | None


SECRET_KEY_FRAGMENTS = (
    "api_key", "apikey", "authorization", "cookie", "database_url", "dsn",
    "jwt", "password", "provider_key", "secret", "access_token",
    "refresh_token", "auth_token", "bearer_token",
)


def assert_checkpoint_safe(value: Any, *, path: str = "state") -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_checkpoint_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS):
                raise ValueError(f"checkpoint state contains forbidden key at {path}.{key}")
            assert_checkpoint_safe(item, path=f"{path}.{key}")
        return
    raise TypeError(f"checkpoint state contains non-JSON type at {path}: {type(value).__name__}")
