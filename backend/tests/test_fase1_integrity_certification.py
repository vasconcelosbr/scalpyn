"""Fase 1 — testes das correções dos Blocos B e da certificação C/D.

Contrato (PROMPT_FASE1, Seção 1, regra 4): toda correção acompanha teste
automatizado que falha antes e passa depois. Cada teste referencia o item
do contrato que cobre.

Sem dependência de Postgres: os pontos fail-closed disparam ANTES de
qualquer I/O, e a certificação é exercitada com uma sessão fake que devolve
resultados enfileirados na ordem exata das queries do serviço.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.ml.dataset_config import (
    BARRIER_CONTRACT_ATR_DYNAMIC_V2,
    LABEL_CONTRACT_VERSION,
    MLDatasetConfigError,
)
from app.services.shadow_trade_service import (
    _apply_barrier_params,
    _build_economic_config_snapshot,
    _resolve_atr_barriers,
    _resolve_barrier_contract_version,
)


# ── B.5 / D1=A — barreiras ATR-dinâmicas e carimbo de contrato ───────────────

def test_atr_barriers_scale_tp_and_sl_under_shared_clamp():
    cfg = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": 1.5,
           "sl_min_pct": 0.5, "sl_max_pct": 3.0}
    tp, sl = _resolve_atr_barriers(1.0, 0.6, 0.0, cfg)
    assert tp == 1.5 and sl == 1.5

    # clamp inferior e superior
    tp_lo, sl_lo = _resolve_atr_barriers(0.1, 0.6, 0.0, cfg)
    assert tp_lo == 0.5 and sl_lo == 0.5
    tp_hi, sl_hi = _resolve_atr_barriers(10.0, 0.6, 0.0, cfg)
    assert tp_hi == 3.0 and sl_hi == 3.0


def test_atr_barriers_without_tp_multiplier_keep_strategies_tp():
    """Sem shadow_atr_multiplier_tp o TP segue o Strategies Module (v1)."""
    cfg = {"sl_atr_multiplier": 1.5, "sl_min_pct": 0.5, "sl_max_pct": 3.0}
    tp, sl = _resolve_atr_barriers(1.0, 0.6, 0.0, cfg)
    assert tp == 0.6 and sl == 1.5


def test_atr_barriers_zero_atr_preserves_inputs():
    tp, sl = _resolve_atr_barriers(0.0, 0.6, 0.9, {"tp_atr_multiplier": 1.5})
    assert (tp, sl) == (0.6, 0.9)


def test_barrier_contract_version_stamp():
    assert (
        _resolve_barrier_contract_version("ATR_DYNAMIC", 1.5)
        == BARRIER_CONTRACT_ATR_DYNAMIC_V2
        == "shadow_atr_dynamic_v2"
    )
    assert _resolve_barrier_contract_version("ATR_DYNAMIC", None) == "shadow_atr_dynamic_v1"
    assert _resolve_barrier_contract_version("FIXED", None) == "shadow_fixed_v1"


def test_apply_barrier_params_merges_tp_multiplier_and_win_threshold():
    """B.1/B.5 — o write path recebe multiplicador de TP e win threshold."""
    merged = _apply_barrier_params({}, {
        "shadow_barrier_mode": "ATR_DYNAMIC",
        "shadow_atr_multiplier_tp": 1.5,
        "shadow_atr_multiplier_sl": 1.5,
        "shadow_barrier_min_pct": 0.5,
        "shadow_barrier_max_pct": 3.0,
        "ml_win_fast_threshold_seconds": 1800,
    })
    assert merged["shadow_barrier_mode"] == "ATR_DYNAMIC"
    assert merged["tp_atr_multiplier"] == 1.5
    assert merged["sl_atr_multiplier"] == 1.5
    assert merged["sl_min_pct"] == 0.5
    assert merged["sl_max_pct"] == 3.0
    assert merged["ml_win_fast_threshold_seconds"] == 1800
    assert "shadow_tp_pct" not in merged


# ── B.1 — config_snapshot completo no write path ─────────────────────────────

_FASE1_SNAPSHOT_KEYS = {
    "barrier_mode", "atr_multiplier_tp", "atr_multiplier_sl",
    "clamp_min", "clamp_max", "win_fast_threshold_seconds",
    "feature_schema_version", "label_contract_version",
    "barrier_contract_version", "capture_contract_version",
}


def _fake_capture():
    return SimpleNamespace(
        snapshot={"rsi": 50.0},
        feature_schema_version="fs_v2",
        capture_contract_version="point-in-time-v1",
        errors=[],
    )


def test_config_snapshot_contains_full_economic_contract():
    """B.1 — snapshot grava barrier_mode, multiplicadores, clamps, win
    threshold e as quatro versões de contrato, todos não-nulos sob v2."""
    snap = _build_economic_config_snapshot(
        tp_pct=1.5, sl_pct=1.5, timeout_candles=48, amount_usdt=100.0,
        ttt_enabled=True, ttt_tp_pct=0.6, ttt_timeout_minutes=240,
        user_config={
            "sl_atr_multiplier": 1.5, "sl_min_pct": 0.5, "sl_max_pct": 3.0,
            "ml_win_fast_threshold_seconds": 1800,
            "ml_fee_roundtrip_pct": 0.2,
        },
        barrier_mode="ATR_DYNAMIC", tp_atr_mult=1.5,
        barrier_contract_version=BARRIER_CONTRACT_ATR_DYNAMIC_V2,
        native_capture=_fake_capture(),
    )
    missing = _FASE1_SNAPSHOT_KEYS - set(snap)
    assert not missing, f"chaves ausentes do contrato B.1: {missing}"
    null_keys = [k for k in _FASE1_SNAPSHOT_KEYS if snap[k] is None]
    assert not null_keys, f"chaves nulas sob shadow_atr_dynamic_v2: {null_keys}"
    assert snap["label_contract_version"] == LABEL_CONTRACT_VERSION
    assert snap["barrier_contract_version"] == "shadow_atr_dynamic_v2"


def test_config_snapshot_is_point_in_time_copy():
    """Snapshot nunca é referência à config viva: mutar a config depois da
    criação não altera o snapshot."""
    cfg = {"sl_atr_multiplier": 1.5, "sl_min_pct": 0.5, "sl_max_pct": 3.0,
           "ml_win_fast_threshold_seconds": 1800}
    snap = _build_economic_config_snapshot(
        tp_pct=1.5, sl_pct=1.5, timeout_candles=48, amount_usdt=100.0,
        ttt_enabled=True, ttt_tp_pct=0.6, ttt_timeout_minutes=240,
        user_config=cfg, barrier_mode="ATR_DYNAMIC", tp_atr_mult=1.5,
        barrier_contract_version=BARRIER_CONTRACT_ATR_DYNAMIC_V2,
        native_capture=_fake_capture(),
    )
    cfg["sl_atr_multiplier"] = 99.0
    cfg["ml_win_fast_threshold_seconds"] = 14400
    assert snap["atr_multiplier_sl"] == 1.5
    assert snap["win_fast_threshold_seconds"] == 1800


# ── B.2 — valid_from obrigatório + guard de montagem ─────────────────────────

class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows=None, scalar=None, one_mapping=None, first=None):
        self._rows = rows or []
        self._scalar = scalar
        self._one_mapping = one_mapping
        self._first = first

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def first(self):
        return self._first

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        outer = self

        class _M:
            def one(self):
                return outer._one_mapping
        return _M()


class _FakeSession:
    """Devolve resultados enfileirados na ordem das chamadas execute()."""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return self._results.pop(0)

    async def commit(self):
        self.committed = True


def _svc():
    from app.services.ml_challenger_service import MLChallengerService
    return MLChallengerService()


@pytest.mark.asyncio
async def test_load_shadow_data_requires_valid_from():
    """B.2 — sem fronteira temporal o treino aborta antes de tocar o banco."""
    with pytest.raises(MLDatasetConfigError, match="missing_dataset_valid_from"):
        await _svc()._load_shadow_data(
            None, uuid4(), 30,
            source_filter=["L1_SPECTRUM"],
            dataset_valid_from=None,
            dataset_query_cutoff=datetime.now(timezone.utc),
            maturity_embargo_margin_minutes=120,
        )


@pytest.mark.asyncio
async def test_load_shadow_data_aborts_on_row_before_valid_from():
    """B.2 — linha pré-fronteira presente no dataset é exceção dura."""
    valid_from = datetime(2026, 7, 14, tzinfo=timezone.utc)
    stale = _FakeRow({
        "shadow_id": "abc",
        "entry_timestamp": valid_from - timedelta(days=2),
    })
    db = _FakeSession([_FakeResult(rows=[stale])])
    with pytest.raises(MLDatasetConfigError, match="dataset_row_before_valid_from"):
        await _svc()._load_shadow_data(
            db, uuid4(), 30,
            source_filter=["L1_SPECTRUM"],
            dataset_valid_from=valid_from,
            dataset_query_cutoff=datetime.now(timezone.utc),
            maturity_embargo_margin_minutes=120,
        )


# ── B.3 — fonte única do win threshold ───────────────────────────────────────

def _patch_config(monkeypatch, svc, ml_config):
    async def _fake_cfg(db):
        return ml_config

    async def _fake_tp(db, user_id):
        return 0.6

    monkeypatch.setattr(svc, "_load_ml_config", _fake_cfg)
    monkeypatch.setattr(svc, "_load_strategy_tp_pct", _fake_tp)


@pytest.mark.asyncio
async def test_train_challengers_rejects_divergent_win_threshold(monkeypatch):
    """B.3 — parâmetro divergente da config é exceção dura (caso v80: 14400)."""
    svc = _svc()
    _patch_config(monkeypatch, svc, {"ml_win_fast_threshold_seconds": 1800})
    with pytest.raises(ValueError, match="win_fast_threshold_divergent"):
        await svc.train_challengers(None, uuid4(), win_fast_threshold_s=14400.0)


@pytest.mark.asyncio
async def test_train_challengers_requires_win_threshold_in_config(monkeypatch):
    """B.3 — chave ausente na config aborta o treino."""
    svc = _svc()
    _patch_config(monkeypatch, svc, {})
    with pytest.raises(MLDatasetConfigError, match="missing_ml_win_fast_threshold_seconds"):
        await svc.train_challengers(None, uuid4())


# ── B.4 — governança de treino inviolável ────────────────────────────────────

def _save_kwargs(**overrides):
    now = datetime.now(timezone.utc)
    metrics = {
        "roc_auc": 0.7, "f1": 0.5, "precision": 0.5, "recall": 0.5, "fpr": 0.1,
        "train_samples": 100, "val_samples": 30,
        "train_from": now.isoformat(), "train_to": now.isoformat(),
        "dataset_query_cutoff": now.isoformat(), "dataset_hash": "deadbeef",
        "label_objective": "positive_net_return",
        "train_sources": ["L1_SPECTRUM"],
    }
    metrics.update(overrides.pop("metrics", {}))
    kwargs = dict(
        model_type="lightgbm",
        model_obj={"stub": True},
        feature_columns=["rsi", "adx"],
        metrics=metrics,
        threshold=0.5,
        profile_id=None,
        user_id=uuid4(),
        model_lane="L1_SPECTRUM",
        win_fast_threshold_s=1800.0,
        dataset_stats={"n_samples": 130, "n_positive": 40,
                       "n_negative": 90, "positive_rate": 0.3077},
    )
    kwargs.update(overrides)
    return kwargs


def _patch_save_deps(monkeypatch, svc):
    async def _fake_version(db):
        return 999

    async def _fake_cfg(db):
        return {}

    monkeypatch.setattr(svc, "_next_version", _fake_version)
    monkeypatch.setattr(svc, "_load_ml_config", _fake_cfg)


@pytest.mark.asyncio
async def test_save_to_db_aborts_without_contract_ids(monkeypatch):
    """B.4 — contract_ids nulos = exceção dura ANTES de qualquer INSERT
    (caso v80: label/feature contract nulos)."""
    svc = _svc()
    _patch_save_deps(monkeypatch, svc)
    kwargs = _save_kwargs(metrics={"train_sources": [], "label_objective": ""})
    db = _FakeSession([])  # nenhum execute esperado: aborta antes
    with pytest.raises(ValueError, match="ml_governance_contract_ids_required"):
        await svc._save_to_db(db, **kwargs)
    assert db.executed == [], "não pode haver INSERT antes do guard"


@pytest.mark.asyncio
async def test_save_to_db_aborts_on_unregistered_lane(monkeypatch):
    """B.4 — lane/source sem contrato em ml_dataset_contracts aborta o treino."""
    svc = _svc()
    _patch_save_deps(monkeypatch, svc)
    db = _FakeSession([_FakeResult(first=None)])  # lookup do contrato → vazio
    with pytest.raises(ValueError, match="training_lane_not_registered"):
        await svc._save_to_db(db, **_save_kwargs())


# ── Blocos C/D — certificação de integridade ─────────────────────────────────

_ML_CONFIG_ROW = {
    "ml_dataset_valid_from": "2026-07-14T00:00:00+00:00",
    "ml_certification_generation_floor": 80,
    "ml_certification_alert_channel": "LOG_ONLY",
    # Fase 1.3 — metas do readiness config-driven.
    "ml_readiness_milestone_rows": 1500,
    "ml_retrain_min_eligible_rows": 3000,
}

_INVARIANT_NAMES = [
    "I01_outcome_casing", "I02_contratos_nulos_em_elegiveis",
    "I03_elegivel_pre_valid_from", "I04_snapshot_incompleto",
    "I05_flag_x_lineage_divergente", "I06_coverage_baixa_em_elegiveis",
    "I07_tp_hit_pnl_negativo", "I08_atr_nulo_em_completed_acima_de_meio_pct",
    "I09_geracao_abaixo_do_piso", "I10_duplicidade_elegivel",
    "I11_holding_negativo",
]


def _invariant_rows(failures=()):
    rows = []
    for name in _INVARIANT_NAMES:
        failed = name in failures
        rows.append(SimpleNamespace(
            invariante=name,
            violacoes=7 if failed else 0,
            status="FAIL" if failed else "PASS",
        ))
    return rows


def _cumulative_mapping():
    return {
        "elegiveis_maturados_pos_boundary": 412,
        "mediana_diaria_7d": 96.0,
        "dias_para_milestone": 16, "dias_para_retrain": 32,
        "calculado_em": datetime.now(timezone.utc),
    }


def _cert_session(failures=(), gate_blocked=0, atr_running=0, previous=None):
    return _FakeSession([
        _FakeResult(rows=[(_ML_CONFIG_ROW,)]),
        _FakeResult(rows=_invariant_rows(failures)),
        _FakeResult(one_mapping=_cumulative_mapping()),
        _FakeResult(scalar=gate_blocked),
        _FakeResult(scalar=atr_running),
        _FakeResult(rows=[previous] if previous else []),
    ])


@pytest.mark.asyncio
async def test_certification_all_pass_is_green():
    from app.services.ml_data_certification_service import run_certification

    db = _cert_session()
    result = await run_certification(db, persist=False)
    assert result["status"] == "GREEN"
    assert result["failed"] == []
    assert result["persisted"] is False
    assert result["cumulative"]["dias_para_milestone"] == 16
    assert result["cumulative"]["dias_para_retrain"] == 32
    # Fase 1.3 — 5000 removido do display; metas expostas com o valor da config.
    assert "dias_para_5000" not in result["cumulative"]
    assert result["cumulative"]["milestone_rows"] == 1500
    assert result["cumulative"]["retrain_gate_rows"] == 3000


@pytest.mark.asyncio
async def test_certification_any_fail_is_red_with_named_invariants():
    from app.services.ml_data_certification_service import run_certification

    db = _cert_session(failures={"I04_snapshot_incompleto"})
    result = await run_certification(db, persist=False)
    assert result["status"] == "RED"
    assert result["failed"] == ["I04_snapshot_incompleto"]
    assert result["alerted"] is True


@pytest.mark.asyncio
async def test_certification_warn_only_is_yellow():
    from app.services.ml_data_certification_service import run_certification

    db = _cert_session(gate_blocked=3)
    # YELLOW consulta o agregado diário após persistir; persist=False pula o
    # INSERT mas a query de resumo ainda roda.
    db._results.append(_FakeResult(scalar=0))
    result = await run_certification(db, persist=False)
    assert result["status"] == "YELLOW"
    assert result["warns"][0]["warn"] == "ML_GATE_BLOCKED_NO_ELIGIBLE_MODEL_FOR_LANE_2H"


@pytest.mark.asyncio
async def test_certification_i09_informative_in_historical_windows():
    """Nota vinculante do Bloco C: I09 informativo fora do job."""
    from app.services.ml_data_certification_service import run_certification

    db = _cert_session(failures={"I09_geracao_abaixo_do_piso"})
    result = await run_certification(db, persist=False, i09_informative=True)
    assert result["status"] == "GREEN"

    db_job = _cert_session(failures={"I09_geracao_abaixo_do_piso"})
    result_job = await run_certification(db_job, persist=False)
    assert result_job["status"] == "RED"


@pytest.mark.asyncio
async def test_certification_alert_is_idempotent_within_window():
    """Bloco D item 7 — mesma assinatura na mesma janela não duplica alerta."""
    from app.services.ml_data_certification_service import run_certification

    previous = SimpleNamespace(
        status="RED",
        run_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        invariants={"failed": ["I04_snapshot_incompleto"]},
    )
    db = _cert_session(failures={"I04_snapshot_incompleto"}, previous=previous)
    result = await run_certification(db, persist=False)
    assert result["status"] == "RED"
    assert result["alerted"] is False


@pytest.mark.asyncio
async def test_certification_requires_generation_floor_config():
    """D3 fail-closed: piso ausente na config aborta a certificação."""
    from app.services.ml_data_certification_service import run_certification

    cfg = {k: v for k, v in _ML_CONFIG_ROW.items()
           if k != "ml_certification_generation_floor"}
    db = _FakeSession([_FakeResult(rows=[(cfg,)])])
    with pytest.raises(RuntimeError, match="missing_ml_certification_generation_floor"):
        await run_certification(db, persist=False)


# ── Fase 1.3 (Passo 1) — readiness config-driven ─────────────────────────────

def _cumulative_params(db):
    """Extrai os params do execute() da query cumulativa registrados no fake."""
    for stmt, params in db.executed:
        if "dias_para_milestone" in stmt:
            return params
    raise AssertionError("query cumulativa não foi executada")


@pytest.mark.asyncio
async def test_readiness_targets_come_from_config_not_hardcoded():
    """Passo 1.4 — mudar a chave de config muda a projeção do readiness: o valor
    da config é o que chega como parâmetro da query cumulativa (não literal)."""
    from app.services.ml_data_certification_service import run_certification

    db = _cert_session()  # config default: milestone=1500, retrain=3000
    await run_certification(db, persist=False)
    params = _cumulative_params(db)
    assert params["milestone_rows"] == 1500
    assert params["retrain_rows"] == 3000

    # Muda os valores na config → os params acompanham (config-driven).
    cfg2 = dict(_ML_CONFIG_ROW,
                ml_readiness_milestone_rows=2000, ml_retrain_min_eligible_rows=4200)
    db2 = _FakeSession([
        _FakeResult(rows=[(cfg2,)]),
        _FakeResult(rows=_invariant_rows()),
        _FakeResult(one_mapping=_cumulative_mapping()),
        _FakeResult(scalar=0),
        _FakeResult(scalar=0),
        _FakeResult(rows=[]),
    ])
    await run_certification(db2, persist=False)
    params2 = _cumulative_params(db2)
    assert params2["milestone_rows"] == 2000
    assert params2["retrain_rows"] == 4200


@pytest.mark.asyncio
async def test_readiness_fail_closed_when_target_key_absent():
    """Passo 1.4 — chave de meta ausente aborta (fail-closed, padrão D3)."""
    from app.services.ml_data_certification_service import run_certification

    cfg = {k: v for k, v in _ML_CONFIG_ROW.items()
           if k != "ml_readiness_milestone_rows"}
    db = _FakeSession([_FakeResult(rows=[(cfg,)])])
    with pytest.raises(ValueError, match="missing_ml_readiness_milestone_rows"):
        await run_certification(db, persist=False)
