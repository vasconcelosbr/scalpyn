from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TenantAIContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: UUID
    user_id: UUID | None
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = Field(default_factory=frozenset)
    provider_key_scope: str
    data_scope: str

    @field_validator("tenant_id")
    @classmethod
    def require_tenant(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("tenant_id is required")
        return value

    @classmethod
    def from_authenticated_user(cls, user_id: UUID, *, role: str = "trader",
                                permissions: set[str] | None = None) -> "TenantAIContext":
        # Scalpyn's current ownership boundary is users.id. This is derived from
        # the signed access token, never accepted from an API payload.
        return cls(
            tenant_id=user_id,
            user_id=user_id,
            roles=(role,),
            permissions=frozenset(permissions or {"ai:analyze", "ai:propose", "ai:shadow"}),
            provider_key_scope=f"user:{user_id}",
            data_scope=f"user:{user_id}",
        )
