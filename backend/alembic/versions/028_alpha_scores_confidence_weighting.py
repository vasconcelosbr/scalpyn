"""alpha_scores_confidence_weighting

Revision ID: 028
Revises: 027
Create Date: 2026-05-01

Add columns for confidence-weighted scoring dual-write mode:
- alpha_score_v2: New confidence-weighted score
- confidence_metrics: JSONB with confidence breakdown
- scoring_version: Track which scoring algorithm was used
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '028'
down_revision = '027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to alpha_scores table for dual-write comparison
    op.add_column('alpha_scores', sa.Column('alpha_score_v2', sa.Float(), nullable=True))
    op.add_column('alpha_scores', sa.Column('confidence_metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('alpha_scores', sa.Column('scoring_version', sa.String(20), nullable=True, server_default='v1'))

    # Create index on scoring_version for filtering queries
    op.create_index('idx_alpha_scores_scoring_version', 'alpha_scores', ['scoring_version'], unique=False)

    # Add comment for documentation
    op.execute("""
        COMMENT ON COLUMN alpha_scores.alpha_score_v2 IS
        'Confidence-weighted alpha score (dual-write mode for gradual rollout)'
    """)
    op.execute("""
        COMMENT ON COLUMN alpha_scores.confidence_metrics IS
        'Confidence breakdown: overall_confidence, category_confidences, low_confidence_rules'
    """)
    op.execute("""
        COMMENT ON COLUMN alpha_scores.scoring_version IS
        'Scoring algorithm version: v1 (legacy), v2 (confidence-weighted), or both (dual-write)'
    """)


def downgrade() -> None:
    op.drop_index('idx_alpha_scores_scoring_version', table_name='alpha_scores')
    op.drop_column('alpha_scores', 'scoring_version')
    op.drop_column('alpha_scores', 'confidence_metrics')
    op.drop_column('alpha_scores', 'alpha_score_v2')
