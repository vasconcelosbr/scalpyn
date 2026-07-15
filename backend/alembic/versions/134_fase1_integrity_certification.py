"""Fase 1 — integridade certificada e monitoração contínua.

Revision ID: 134_fase1_integrity_cert
Revises: 133_native_feature_capture

Nota: revision id mantido <= 32 chars (alembic_version.version_num é
VARCHAR(32)); "134_fase1_integrity_certification" (33) causava
StringDataRightTruncationError no UPDATE da alembic_version.
Create Date: 2026-07-14

Escopo (contrato PROMPT_FASE1, decisões D1=A, D3=80, D4=1.5/[0.5,3.0]):

1. ml_data_certification_runs (DECISÃO PRÉ-AUTORIZADA A-2):
   ml_dataset_readiness_reports existe mas tem estrutura incompatível com o
   payload do job de certificação (Bloco D). Uma linha por execução do job,
   com status agregado GREEN/YELLOW/RED, resultado literal por invariante
   (jsonb) e cumulativo (jsonb).

2. ml_training_dataset.win_threshold_s (B.3): o valor de win threshold
   efetivamente usado no treino é gravado também no registro de dataset,
   além de ml_models.notes/target_window_seconds.

3. Registro de contratos de dataset (B.4): lane/source de treino passam a
   ser restritos aos pares registrados em ml_dataset_contracts. Semeia os
   pares em operação hoje; lane nova exige contrato novo registrado antes.

4. Contrato de barreira v2 (B.5, D1=A): registra em ml_label_contracts a
   descrição do label vigente sob o contrato de barreira shadow_atr_dynamic_v2
   (TP e SL ATR-dinâmicos, multiplicadores 1.5, clamp [0.5, 3.0]).

Nenhum UPDATE/DELETE em shadow_trades. Nenhum backfill.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "134_fase1_integrity_cert"
down_revision = "133_native_feature_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A-2 — tabela de execuções do job de certificação (Bloco D)
    op.create_table(
        "ml_data_certification_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "run_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("window_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("window_to", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),  # GREEN | YELLOW | RED
        sa.Column("invariants", postgresql.JSONB, nullable=False),
        sa.Column("cumulative", postgresql.JSONB),
    )
    op.create_index(
        "ix_ml_data_certification_runs_run_at",
        "ml_data_certification_runs",
        ["run_at"],
    )

    # B.3 — valor usado gravado no registro de dataset
    op.add_column(
        "ml_training_dataset",
        sa.Column("win_threshold_s", sa.Integer, nullable=True),
    )

    # B.4 — contratos de dataset registrados (lane/source em operação hoje).
    # O guard em MLChallengerService._save_to_db recusa qualquer par
    # (model_lane, source_filter) sem linha aqui.
    op.execute(sa.text("""
        INSERT INTO ml_dataset_contracts (id, source_filter, model_lane, description)
        VALUES
            ('ds_l1_spectrum_atrdyn_v2', 'L1_SPECTRUM', 'L1_SPECTRUM',
             'Fase 1 (D1=A) — população canônica de treino: source=L1_SPECTRUM, '
             'barrier_mode=ATR_DYNAMIC, barrier_contract=shadow_atr_dynamic_v2 '
             '(TP=ATR×shadow_atr_multiplier_tp, SL=ATR×shadow_atr_multiplier_sl, '
             'clamp [shadow_barrier_min_pct, shadow_barrier_max_pct]); '
             'label=positive_net_return_v1; win threshold exclusivamente via '
             'config ml_win_fast_threshold_seconds; fronteira ml_dataset_valid_from.'),
            ('ds_l3_profile_v1', 'L3', 'L3_PROFILE',
             'Lane CatBoost L3 aprovados (pré-Fase 1, registrado para continuidade).'),
            ('ds_l3_lab_profile_v1', 'L3_LAB', 'L3_LAB_PROFILE',
             'Lane CatBoost Strategy Lab (pré-Fase 1, registrado para continuidade).'),
            ('ds_l3_intelligence_v1', 'L3_REJECTED', 'L3_INTELLIGENCE',
             'Lane diagnóstica de rejeitados (pré-Fase 1, registrado para continuidade).'),
            ('ds_l3_approved_intel_v1', 'L3', 'L3_APPROVED_INTELLIGENCE',
             'Lane advisory de aprovados (pré-Fase 1, registrado para continuidade).'),
            ('ds_l3_contextual_intel_v1', 'L3,L3_REJECTED', 'L3_CONTEXTUAL_INTELLIGENCE',
             'Lane advisory contextual (pré-Fase 1, registrado para continuidade).')
        ON CONFLICT DO NOTHING
    """))

    # B.5 (D1=A) — contrato de label sob o contrato de barreira v2
    op.execute(sa.text("""
        INSERT INTO ml_label_contracts (
            id, name, version, description, sql_expression, target_window_seconds
        ) VALUES (
            'positive_net_return_v1', 'positive_net_return', '1.0',
            'Label positivo quando o retorno líquido de fees é positivo. '
            'Barreira: shadow_atr_dynamic_v2 (D1=A, Fase 1) — TP e SL '
            'ATR-dinâmicos e simétricos: TP=ATR×1.5 (shadow_atr_multiplier_tp), '
            'SL=ATR×1.5 (shadow_atr_multiplier_sl), clamp [0.5, 3.0] '
            '(shadow_barrier_min_pct/shadow_barrier_max_pct). Substitui o '
            'artefato estrutural do v1 (TP fixo 0.6% sob SL ATR-dinâmico). '
            'valid_from = ml_dataset_valid_from (timestamp do deploy da Fase 1); '
            'dados anteriores permanecem intocados, apenas deixam de ser '
            'população canônica. win threshold: ml_win_fast_threshold_seconds.',
            'net_return_pct > 0', NULL
        )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM ml_label_contracts WHERE id = 'positive_net_return_v1'"
    ))
    op.execute(sa.text("""
        DELETE FROM ml_dataset_contracts WHERE id IN (
            'ds_l1_spectrum_atrdyn_v2', 'ds_l3_profile_v1', 'ds_l3_lab_profile_v1',
            'ds_l3_intelligence_v1', 'ds_l3_approved_intel_v1',
            'ds_l3_contextual_intel_v1'
        )
    """))
    op.drop_column("ml_training_dataset", "win_threshold_s")
    op.drop_index(
        "ix_ml_data_certification_runs_run_at",
        table_name="ml_data_certification_runs",
    )
    op.drop_table("ml_data_certification_runs")
