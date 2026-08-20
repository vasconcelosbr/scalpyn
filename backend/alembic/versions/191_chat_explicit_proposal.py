"""Version the deterministic explicit governed-proposal path.

Revision ID: 191_chat_explicit_proposal
Revises: 190_chat_governed_readback
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "191_chat_explicit_proposal"
down_revision = "190_chat_governed_readback"
branch_labels = None
depends_on = None

GRAPH_NAMESPACE = uuid.UUID("a42c5ab1-1bda-5e45-ae66-554315834a7d")
APPROVED_AT = datetime(2026, 8, 20, 19, 0, tzinfo=timezone.utc)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _expanded_graph(previous: dict[str, object]) -> dict[str, object]:
    content = deepcopy(previous)
    content["semantic_version"] = "1.3.0"
    content["tool_policy_version"] = "analysis-chat-governed-write-policy-v3"
    return content


def upgrade() -> None:
    bind = op.get_bind()
    previous = bind.execute(sa.text("""
        SELECT graph_key, state_schema_version, node_manifest, edge_manifest
          FROM ai_graph_definitions
         WHERE graph_key = 'analysis-chat-v1'
           AND semantic_version = '1.2.0'
           AND status = 'APPROVED'
    """)).mappings().one()
    graph = _expanded_graph(dict(previous))
    bind.execute(sa.text("""
        INSERT INTO ai_graph_definitions (
            id, graph_key, semantic_version, state_schema_version, status,
            content_hash, code_revision, node_manifest, edge_manifest,
            tool_policy_version, created_at, approved_at
        ) VALUES (
            CAST(:id AS uuid), :graph_key, :semantic_version, :state_schema_version,
            'APPROVED', :content_hash, :code_revision, CAST(:node_manifest AS jsonb),
            CAST(:edge_manifest AS jsonb), :tool_policy_version, :created_at, :approved_at
        ) ON CONFLICT (graph_key, semantic_version) DO UPDATE SET
            state_schema_version = EXCLUDED.state_schema_version,
            status = 'APPROVED',
            content_hash = EXCLUDED.content_hash,
            code_revision = EXCLUDED.code_revision,
            node_manifest = EXCLUDED.node_manifest,
            edge_manifest = EXCLUDED.edge_manifest,
            tool_policy_version = EXCLUDED.tool_policy_version,
            approved_at = EXCLUDED.approved_at
    """), {
        "id": str(uuid.uuid5(GRAPH_NAMESPACE, "analysis-chat-v1@1.3.0")),
        **graph,
        "content_hash": _canonical_hash(graph),
        "code_revision": revision,
        "node_manifest": json.dumps(graph["node_manifest"]),
        "edge_manifest": json.dumps(graph["edge_manifest"]),
        "created_at": APPROVED_AT,
        "approved_at": APPROVED_AT,
    })


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM ai_graph_definitions
         WHERE graph_key = 'analysis-chat-v1'
           AND semantic_version = '1.3.0'
    """))
