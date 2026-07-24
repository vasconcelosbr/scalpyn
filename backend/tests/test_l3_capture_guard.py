"""P1 — guard anti-silêncio de captura direcional (auditoria captura L3 2026-07-24).

Testa as funções puras do guard: o resolver config-driven (Zero Hardcode,
tolerante) e o avaliador de warns por source. A regressão que este guard cobre
(captura L3 caindo para 2,1% por ~1 semana) passou despercebida por ausência de
qualquer verificação — os testes garantem que baixo capture vira WARN e que
config ausente/inválida NÃO usa threshold literal.
"""
from app.services.ml_data_certification_service import (
    _resolve_l3_capture_guard,
    evaluate_l3_capture_warns,
    _l3_capture_sql,
)
from app.ml.feature_contract_v2 import REQUIRED_DIRECTIONAL_FEATURES


# ── resolver (Zero Hardcode, tolerante — None em vez de crash) ────────────────

def test_resolver_present():
    guard = _resolve_l3_capture_guard({
        "ml_l3_capture_min_directional_rate": 0.90,
        "ml_l3_capture_window_hours": 26,
        "ml_l3_capture_min_sample": 50,
    })
    assert guard == {"min_rate": 0.90, "window_hours": 26, "min_sample": 50}


def test_resolver_missing_keys_returns_none():
    assert _resolve_l3_capture_guard({}) is None
    assert _resolve_l3_capture_guard({"ml_l3_capture_min_directional_rate": 0.9}) is None


def test_resolver_invalid_values_return_none():
    # bool não é aceito (isinstance bool passa em int)
    assert _resolve_l3_capture_guard({
        "ml_l3_capture_min_directional_rate": True,
        "ml_l3_capture_window_hours": 26,
        "ml_l3_capture_min_sample": 50,
    }) is None
    # rate fora de (0, 1]
    assert _resolve_l3_capture_guard({
        "ml_l3_capture_min_directional_rate": 1.5,
        "ml_l3_capture_window_hours": 26,
        "ml_l3_capture_min_sample": 50,
    }) is None
    # janela não-positiva
    assert _resolve_l3_capture_guard({
        "ml_l3_capture_min_directional_rate": 0.9,
        "ml_l3_capture_window_hours": 0,
        "ml_l3_capture_min_sample": 50,
    }) is None
    # não-numérico
    assert _resolve_l3_capture_guard({
        "ml_l3_capture_min_directional_rate": "x",
        "ml_l3_capture_window_hours": 26,
        "ml_l3_capture_min_sample": 50,
    }) is None


# ── avaliador de warns ───────────────────────────────────────────────────────

_GUARD = {"min_rate": 0.90, "window_hours": 26, "min_sample": 50}


def test_below_threshold_emits_warn():
    # regressão L3 real: 2,1%
    rows = [{"source": "L3", "n": 1553, "with_dir": 33}]
    warns = evaluate_l3_capture_warns(_GUARD, rows)
    assert len(warns) == 1
    assert warns[0]["warn"] == "DIRECTIONAL_CAPTURE_BELOW_THRESHOLD"
    assert warns[0]["source"] == "L3"
    assert warns[0]["rate"] < 0.90


def test_above_threshold_no_warn():
    rows = [{"source": "L1_SPECTRUM", "n": 700, "with_dir": 700}]
    assert evaluate_l3_capture_warns(_GUARD, rows) == []


def test_small_sample_is_skipped():
    # abaixo do min_sample → ignorado (não flapa em baixo volume), mesmo com rate 0
    rows = [{"source": "L3", "n": 10, "with_dir": 0}]
    assert evaluate_l3_capture_warns(_GUARD, rows) == []


def test_multiple_sources_only_bad_ones_warn():
    rows = [
        {"source": "L3", "n": 1553, "with_dir": 33},       # 2,1% → warn
        {"source": "L3_LAB", "n": 2735, "with_dir": 2735},  # 100% → ok
        {"source": "L1_SPECTRUM", "n": 701, "with_dir": 701},  # 100% → ok
    ]
    warns = evaluate_l3_capture_warns(_GUARD, rows)
    assert [w["source"] for w in warns] == ["L3"]


# ── SQL builder ──────────────────────────────────────────────────────────────

def test_sql_includes_all_directional_keys():
    sql = str(_l3_capture_sql())
    for feat in REQUIRED_DIRECTIONAL_FEATURES:
        assert feat in sql, f"{feat} ausente no SQL do guard"
    # forma-função (evita ambiguidade do operador ? no driver)
    assert "jsonb_exists(features_snapshot" in sql
