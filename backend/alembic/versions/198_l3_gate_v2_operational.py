"""Allow governed operational promotion of L3 gate v2.

Revision ID: 198_l3_gate_v2_operational
Revises: 197_l3_gate_v2_evaluations
"""

from alembic import op


revision = "198_l3_gate_v2_operational"
down_revision = "197_l3_gate_v2_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_l3_gate_v2_observational_only",
        "l3_gate_v2_evaluations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_l3_gate_v2_operational_payload_match",
        "l3_gate_v2_evaluations",
        "operational_effect = COALESCE((payload ->> 'operational_effect')::boolean, false)",
    )
    op.create_check_constraint(
        "ck_l3_gate_v2_operational_metadata",
        "l3_gate_v2_evaluations",
        "(operational_effect AND "
        "payload ->> 'promotion_status' = 'OPERATIONAL' AND "
        "payload ->> 'operational_decision' IN ('ALLOW', 'BLOCK')) OR "
        "(NOT operational_effect AND "
        "COALESCE(payload ->> 'promotion_status', 'SHADOW_ONLY') <> 'OPERATIONAL')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_l3_gate_v2_operational_metadata",
        "l3_gate_v2_evaluations",
        type_="check",
    )
    op.drop_constraint(
        "ck_l3_gate_v2_operational_payload_match",
        "l3_gate_v2_evaluations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_l3_gate_v2_observational_only",
        "l3_gate_v2_evaluations",
        "operational_effect = false",
    )
