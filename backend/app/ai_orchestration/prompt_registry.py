from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import AIErrorCode, fail
from .hashing import canonical_hash


class PromptVersion(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID = Field(default_factory=uuid4)
    prompt_key: str
    semantic_version: str
    status: str
    system_template: str
    user_template: str
    input_schema_json: dict[str, Any]
    output_schema_json: dict[str, Any]
    tool_policy_json: dict[str, Any]
    provider_constraints_json: dict[str, Any]
    content_hash: str
    created_by: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_by: UUID | None = None
    approved_at: datetime | None = None

    @classmethod
    def create(cls, **kwargs) -> "PromptVersion":
        payload = {key: kwargs[key] for key in (
            "prompt_key", "semantic_version", "system_template", "user_template",
            "input_schema_json", "output_schema_json", "tool_policy_json", "provider_constraints_json",
        )}
        return cls(content_hash=canonical_hash(payload), **kwargs)

    def verify_hash(self) -> None:
        expected = canonical_hash({key: getattr(self, key) for key in (
            "prompt_key", "semantic_version", "system_template", "user_template",
            "input_schema_json", "output_schema_json", "tool_policy_json", "provider_constraints_json",
        )})
        if expected != self.content_hash:
            raise fail(AIErrorCode.PROMPT_HASH_MISMATCH, "Prompt content hash does not match the immutable version")


class PromptRegistry:
    def __init__(self, versions: Iterable[PromptVersion]):
        self._versions = {(v.prompt_key, v.semantic_version): v for v in versions}

    def resolve(self, prompt_key: str, semantic_version: str | None = None) -> PromptVersion:
        matches = [v for (key, _), v in self._versions.items() if key == prompt_key]
        if semantic_version:
            matches = [v for v in matches if v.semantic_version == semantic_version]
        if not matches:
            raise fail(AIErrorCode.PROMPT_NOT_FOUND, "Prompt version was not found")
        version = sorted(matches, key=lambda v: v.semantic_version)[-1]
        if version.status != "APPROVED":
            raise fail(AIErrorCode.PROMPT_NOT_FOUND, "Prompt version is not approved")
        version.verify_hash()
        return version

    @staticmethod
    def render(version: PromptVersion, values: dict[str, Any]) -> tuple[str, str]:
        version.verify_hash()
        try:
            return version.system_template.format_map(values), version.user_template.format_map(values)
        except KeyError as exc:
            raise fail(AIErrorCode.PROMPT_NOT_FOUND, f"Prompt input is missing field {exc.args[0]}") from exc
