"""Normalize chat provider lineage and reconcile audited usage.

Revision ID: 162_chat_output_normalize
Revises: 161_output_budget_repair
"""

from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa


revision = "162_chat_output_normalize"
down_revision = "161_output_budget_repair"
branch_labels = None
depends_on = None

SAFE_MESSAGE = "Provider returned an inconsistent structured response"
SAFE_HASH = hashlib.sha256(SAFE_MESSAGE.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE ai_analysis_messages AS message
           SET tokens_input=usage.tokens_input,
               tokens_output=usage.tokens_output,
               cost_usd=usage.actual_cost
          FROM ai_usage_records AS usage
         WHERE usage.ai_request_id=message.ai_request_id
           AND usage.tenant_id=message.tenant_id
           AND message.role='ASSISTANT'
           AND (message.tokens_input IS NULL
                OR message.tokens_output IS NULL
                OR message.cost_usd IS NULL)
    """))
    bind.execute(sa.text("""
        UPDATE ai_analysis_conversations AS conversation
           SET total_tokens_input=totals.tokens_input,
               total_tokens_output=totals.tokens_output,
               total_cost_usd=totals.cost_usd,
               updated_at=NOW(),
               lock_version=conversation.lock_version + 1
          FROM (
              SELECT conversation_id,
                     COALESCE(SUM(tokens_input), 0) AS tokens_input,
                     COALESCE(SUM(tokens_output), 0) AS tokens_output,
                     COALESCE(SUM(cost_usd), 0) AS cost_usd
                FROM ai_analysis_messages
               WHERE role='ASSISTANT'
               GROUP BY conversation_id
          ) AS totals
         WHERE totals.conversation_id=conversation.id
    """))
    bind.execute(sa.text("""
        UPDATE ai_graph_runs
           SET error_kind='PROVIDER_OUTPUT_FAILED',
               last_error_safe_message=:safe_message,
               provider_transport_attempted=TRUE,
               updated_at=NOW()
         WHERE last_error_code='ANALYSIS_CHAT_PARENT_ID_OUTPUT_MISMATCH'
    """).bindparams(safe_message=SAFE_MESSAGE))
    bind.execute(sa.text("""
        UPDATE ai_jobs
           SET last_error_safe_message=:safe_message
         WHERE last_error_code='ANALYSIS_CHAT_PARENT_ID_OUTPUT_MISMATCH'
    """).bindparams(safe_message=SAFE_MESSAGE))
    bind.execute(sa.text("""
        UPDATE ai_analysis_messages AS message
           SET content=:safe_message,
               content_hash=:safe_hash,
               provider_transport_attempted=TRUE
          FROM ai_graph_runs AS run
         WHERE run.id=message.graph_run_id
           AND run.last_error_code='ANALYSIS_CHAT_PARENT_ID_OUTPUT_MISMATCH'
           AND message.role='ASSISTANT'
           AND message.status='FAILED'
    """).bindparams(safe_message=SAFE_MESSAGE, safe_hash=SAFE_HASH))


def downgrade() -> None:
    bind = op.get_bind()
    previous_message = "Provider transport failed after the request was attempted"
    previous_hash = hashlib.sha256(previous_message.encode("utf-8")).hexdigest()
    bind.execute(sa.text("""
        UPDATE ai_graph_runs
           SET error_kind='PROVIDER_TRANSPORT_FAILED',
               last_error_safe_message=:safe_message,
               updated_at=NOW()
         WHERE last_error_code='ANALYSIS_CHAT_PARENT_ID_OUTPUT_MISMATCH'
    """).bindparams(safe_message=previous_message))
    bind.execute(sa.text("""
        UPDATE ai_jobs
           SET last_error_safe_message=:safe_message
         WHERE last_error_code='ANALYSIS_CHAT_PARENT_ID_OUTPUT_MISMATCH'
    """).bindparams(safe_message=previous_message))
    bind.execute(sa.text("""
        UPDATE ai_analysis_messages AS message
           SET content=:safe_message,
               content_hash=:safe_hash
          FROM ai_graph_runs AS run
         WHERE run.id=message.graph_run_id
           AND run.last_error_code='ANALYSIS_CHAT_PARENT_ID_OUTPUT_MISMATCH'
           AND message.role='ASSISTANT'
           AND message.status='FAILED'
    """).bindparams(safe_message=previous_message, safe_hash=previous_hash))
