"""P2 Fase 1.7 — regressão do job de certificação morto.

O `_CUMULATIVE_SQL` usava `:milestone_rows::numeric` / `:retrain_rows::numeric`.
O padrão `:param::cast` quebra o parser de bind-param do SQLAlchemy (o nome do
param sai truncado/errado), o `:` vaza literal para o asyncpg e a task explode
com `PostgresSyntaxError: syntax error at or near ":"`. O fix usa
`CAST(:param AS numeric)`. Este teste trava o contrato de binds.
"""
from sqlalchemy import text

from app.services.ml_data_certification_service import _CUMULATIVE_SQL


def test_cumulative_sql_binds_all_params_correctly():
    binds = set(_CUMULATIVE_SQL._bindparams.keys())
    # Todos os 5 params passados em run_certification devem ser reconhecidos.
    assert binds == {"src", "bmode", "valid_from", "milestone_rows", "retrain_rows"}, binds


def test_cumulative_sql_has_no_colon_cast_on_binds():
    # Nenhum `:param::cast` (o padrão que quebrou o asyncpg) permanece.
    raw = str(_CUMULATIVE_SQL)
    assert ":milestone_rows::" not in raw
    assert ":retrain_rows::" not in raw
    assert "CAST(:milestone_rows AS numeric)" in raw
    assert "CAST(:retrain_rows AS numeric)" in raw


def test_colon_cast_pattern_reproduces_the_bug():
    # Demonstra a causa-raiz: `:name::cast` NÃO produz o bind param `name`.
    broken = text("SELECT CEIL(:milestone_rows::numeric) AS x")
    assert "milestone_rows" not in broken._bindparams  # nome sai errado/truncado
    fixed = text("SELECT CEIL(CAST(:milestone_rows AS numeric)) AS x")
    assert "milestone_rows" in fixed._bindparams
