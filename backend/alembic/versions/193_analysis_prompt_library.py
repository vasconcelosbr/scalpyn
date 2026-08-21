"""Add the shared, versioned Intelligence Runs prompt library.

Revision ID: 193_analysis_prompt_library
Revises: 192_chat_all_governed_domains
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "193_analysis_prompt_library"
down_revision = "192_chat_all_governed_domains"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("773bfb77-8c83-52d3-a869-0bbad37cbb89")
PROMPT_ID = uuid.uuid5(PROMPT_NAMESPACE, "auditor-causa-raiz-entradas-ruins-l3")
VERSION_ID = uuid.uuid5(PROMPT_NAMESPACE, "auditor-causa-raiz-entradas-ruins-l3:v1")
PROMPT_NAME = "Auditor de Causa Raiz de Entradas Ruins L3"
PROMPT_FILENAME = "Prompt — Auditor de Causa Raiz de Entradas Ruins L3.md"
PROMPT_HASH = "b739978f1a7a496241e87fbf83c39abfa3eafc3c76e10b7e1ae6d01772296010"
SEEDED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _seed_content() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "ai_orchestration"
        / "analysis_prompts"
        / "auditor_causa_raiz_entradas_ruins_l3.md"
    )
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if content.endswith("\n"):
        content = content[:-1]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if digest != PROMPT_HASH:
        raise RuntimeError(f"ANALYSIS_PROMPT_SEED_HASH_MISMATCH:{digest}")
    return content


def upgrade() -> None:
    op.create_table(
        "ai_analysis_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("name_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("archived_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True)),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_ai_analysis_prompt_status"),
        sa.UniqueConstraint("name_key", name="uq_ai_analysis_prompt_name_key"),
    )
    op.create_index(
        "ix_ai_analysis_prompt_status_updated",
        "ai_analysis_prompts",
        ["status", "updated_at"],
    )
    op.create_table(
        "ai_analysis_prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "prompt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_analysis_prompts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name_snapshot", sa.String(160), nullable=False),
        sa.Column("description_snapshot", sa.Text()),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_filename", sa.String(255)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version_number >= 1", name="ck_ai_analysis_prompt_version_number"),
        sa.CheckConstraint(
            "source_type IN ('UPLOAD_MD', 'PASTE', 'SEEDED')",
            name="ck_ai_analysis_prompt_source_type",
        ),
        sa.CheckConstraint(
            "char_length(content_markdown) BETWEEN 1 AND 100000",
            name="ck_ai_analysis_prompt_content_length",
        ),
        sa.UniqueConstraint("prompt_id", "version_number", name="uq_ai_analysis_prompt_version"),
    )
    op.create_index(
        "ix_ai_analysis_prompt_version_prompt_created",
        "ai_analysis_prompt_versions",
        ["prompt_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_ai_analysis_prompt_current_version",
        "ai_analysis_prompts",
        "ai_analysis_prompt_versions",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "ai_requests",
        sa.Column("analysis_prompt_version_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_ai_request_analysis_prompt_version",
        "ai_requests",
        "ai_analysis_prompt_versions",
        ["analysis_prompt_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    content = _seed_content()
    bind = op.get_bind()
    bind.execute(
        sa.text("""
            INSERT INTO ai_analysis_prompts (
                id, name, name_key, status, created_at, updated_at
            ) VALUES (
                :id, :name, :name_key, 'ACTIVE', :created_at, :created_at
            )
        """),
        {
            "id": PROMPT_ID,
            "name": PROMPT_NAME,
            "name_key": PROMPT_NAME.casefold(),
            "created_at": SEEDED_AT,
        },
    )
    bind.execute(
        sa.text("""
            INSERT INTO ai_analysis_prompt_versions (
                id, prompt_id, version_number, name_snapshot, description_snapshot,
                content_markdown, content_hash, source_type, source_filename, created_at
            ) VALUES (
                :id, :prompt_id, 1, :name, :description,
                :content, :content_hash, 'SEEDED', :filename, :created_at
            )
        """),
        {
            "id": VERSION_ID,
            "prompt_id": PROMPT_ID,
            "name": PROMPT_NAME,
            "description": "Auditoria L3 de causa raiz para entradas ruins, com controle TP/SL e análise sem leakage.",
            "content": content,
            "content_hash": PROMPT_HASH,
            "filename": PROMPT_FILENAME,
            "created_at": SEEDED_AT,
        },
    )
    bind.execute(
        sa.text("UPDATE ai_analysis_prompts SET current_version_id = :version_id WHERE id = :prompt_id"),
        {"version_id": VERSION_ID, "prompt_id": PROMPT_ID},
    )


def downgrade() -> None:
    op.drop_constraint("fk_ai_request_analysis_prompt_version", "ai_requests", type_="foreignkey")
    op.drop_column("ai_requests", "analysis_prompt_version_id")
    op.drop_constraint("fk_ai_analysis_prompt_current_version", "ai_analysis_prompts", type_="foreignkey")
    op.drop_index("ix_ai_analysis_prompt_version_prompt_created", table_name="ai_analysis_prompt_versions")
    op.drop_table("ai_analysis_prompt_versions")
    op.drop_index("ix_ai_analysis_prompt_status_updated", table_name="ai_analysis_prompts")
    op.drop_table("ai_analysis_prompts")
