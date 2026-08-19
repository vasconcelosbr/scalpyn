"""Add failure_diagnostics column to ai_graph_runs (AUD-IR-CTR-001 4.3/L14).

Revision ID: 187_ir_failure_diagnostics
Revises: 186_deepseek_output_16k
Create Date: 2026-08-19

For every FAILED graph run, ai_results never gets a row and
last_error_safe_message is a deliberately generic string shared across
distinct error codes (e.g. "The systemic AI graph could not complete" for
both AI_INPUT_RESERVATION_EXCEEDED and LANGGRAPH_REAL_PROVIDER_CANARY_DISABLED).
Meanwhile systemic_langgraph_bridge.execute_prepared_request already builds a
safe diagnostic dict (provider_stop_reason, schema_error_path,
schema_validator, provider_response_ref) on every provider/validation
failure -- it was just discarded before persistence instead of attached to
the exception. This column captures that dict. It never holds a raw prompt
or provider response body; see errors.ProviderOutputError.diagnostics for
what is and is not allowed into it.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "187_ir_failure_diagnostics"
down_revision = "186_deepseek_output_16k"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_graph_runs",
        sa.Column("failure_diagnostics", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_graph_runs", "failure_diagnostics")
