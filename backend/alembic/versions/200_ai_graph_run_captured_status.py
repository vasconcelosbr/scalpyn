"""Allow durable capture-only canonical Shadow Portfolio runs.

Revision ID: 200_ai_graph_run_captured
Revises: 199_shadow_full_canonical
"""

from alembic import op


revision = "200_ai_graph_run_captured"
down_revision = "199_shadow_full_canonical"
branch_labels = None
depends_on = None


_STATUS_VALUES = (
    "'QUEUED','RUNNING','INTERRUPTED','WAITING_SHADOW','COMPLETED',"
    "'FAILED','CANCELLED','CAPTURED'"
)
_LEGACY_STATUS_VALUES = (
    "'QUEUED','RUNNING','INTERRUPTED','WAITING_SHADOW','COMPLETED',"
    "'FAILED','CANCELLED'"
)


def upgrade() -> None:
    op.drop_constraint("ck_ai_graph_run_status", "ai_graph_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_graph_run_status",
        "ai_graph_runs",
        f"status IN ({_STATUS_VALUES})",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE ai_graph_runs "
        "SET status = 'COMPLETED', "
        "terminal_reason = COALESCE(terminal_reason, 'SHADOW_CANONICAL_CAPTURE_ONLY') "
        "WHERE status = 'CAPTURED'"
    )
    op.drop_constraint("ck_ai_graph_run_status", "ai_graph_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_graph_run_status",
        "ai_graph_runs",
        f"status IN ({_LEGACY_STATUS_VALUES})",
    )
