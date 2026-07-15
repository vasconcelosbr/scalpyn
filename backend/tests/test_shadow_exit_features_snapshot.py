"""Cobertura do capture de indicadores na SAÍDA do Shadow Portfolio (Task #306).

Cenários:

* ``test_build_full_flat_snapshot_returns_flat_scalars`` — o helper canônico
  ``indicators_provider.build_full_flat_snapshot`` devolve um dict flat
  ``{key: scalar}`` com TODAS as chaves merged do símbolo, filtrando
  dict/list defensivamente (invariante Task #290).

* ``test_build_full_flat_snapshot_empty_when_symbol_missing`` — quando o
  provider não tem indicadores merged para o símbolo, devolve ``{}`` sem
  raise (contrato best-effort).

* ``test_capture_exit_features_uses_canonical_helper`` — ``_capture_exit_features``
  delega para o helper canônico e grava o snapshot flat completo em
  ``shadow.features_snapshot_exit``.

* ``test_capture_exit_features_writes_marker_when_empty`` — quando o helper
  devolve vazio, gravamos um marcador ``_capture_failed`` para que o
  frontend distinga "snapshot indisponível" de "ainda não capturado".

* ``test_record_as_simulation_uses_exit_snapshot`` — quando a shadow tem
  ``features_snapshot_exit`` completo, ``record_as_simulation`` persiste
  ESSE snapshot em ``trade_simulations.features_snapshot`` (não o de
  entrada) — alimenta o XGBoost com features simétricas ao bloco
  "Indicadores na SAÍDA" do modal.

* ``test_record_as_simulation_falls_back_to_entry_on_capture_failure`` —
  quando o exit snapshot é o marcador ``_capture_failed`` (provider sem
  dados no fechamento) ou NULL (trade antigo, pre-#306), o caller cai
  para o snapshot de entrada para não perder a linha do DatasetBuilder.
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import indicators_provider  # noqa: E402
from app.utils.indicator_merge import MergedIndicators  # noqa: E402


def _make_merged(values: dict) -> MergedIndicators:
    m = MergedIndicators()
    m.values = dict(values)
    m.meta = {
        k: {"group": "structural", "stale": False, "timestamp": None}
        for k in values
    }
    return m


# ── build_full_flat_snapshot ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_full_flat_snapshot_returns_flat_scalars():
    """Todas as chaves merged devolvidas flat, sem dict/list."""
    merged = _make_merged({
        "rsi": 55.2,
        "adx": 22.1,
        "macd_histogram": -0.0023,
        "taker_ratio": 0.61,
        "volume_delta": 12345.0,
        "ema9_gt_ema21": True,
        "missing_value": None,
        # Defensive: provider may surface bad data — must be filtered
        "bogus_dict": {"hist": 0.1},
        "bogus_list": [1, 2],
    })

    async def _fake_get_merged(db, symbols, **kw):
        return {symbols[0]: merged}

    with patch.object(
        indicators_provider, "get_merged_indicators", new=_fake_get_merged
    ):
        out = await indicators_provider.build_full_flat_snapshot(
            db=None, symbol="BTC_USDT"
        )

    # Same key set as merged.values minus dict/list entries.
    assert set(out.keys()) == {
        "rsi", "adx", "macd_histogram", "taker_ratio",
        "volume_delta", "ema9_gt_ema21", "missing_value",
    }
    # All values are scalar (int/float/bool/None) — ML invariant (Task #290).
    for v in out.values():
        assert isinstance(v, (int, float, bool)) or v is None


@pytest.mark.asyncio
async def test_build_full_flat_snapshot_empty_when_symbol_missing():
    """Sem indicadores merged para o símbolo → dict vazio (não raise)."""
    async def _fake_get_merged(db, symbols, **kw):
        return {}

    with patch.object(
        indicators_provider, "get_merged_indicators", new=_fake_get_merged
    ):
        out = await indicators_provider.build_full_flat_snapshot(
            db=None, symbol="UNKNOWN_USDT"
        )
    assert out == {}


# ── _capture_exit_features ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_exit_features_uses_canonical_helper():
    """Delega para o helper canônico e grava o snapshot completo."""
    from app.tasks import shadow_trade_monitor

    full_snap = {
        "rsi": 55.0, "adx": 22.0, "macd_histogram": 0.001,
        "taker_ratio": 0.7, "volume_delta": 1000.0,
        "ema9": 1.23, "ema21": 1.20, "ema50": 1.18, "ema200": 1.10,
        "vwap": 1.22, "bb_upper": 1.30, "bb_lower": 1.10,
    }
    shadow = SimpleNamespace(
        id=uuid4(), symbol="BTC_USDT", features_snapshot_exit=None,
    )

    async def _fake_helper(db, symbol, **kw):
        assert symbol == "BTC_USDT"
        assert kw.get("include_stale") is True
        return dict(full_snap)

    with patch.object(
        shadow_trade_monitor.indicators_provider,
        "build_full_flat_snapshot",
        new=_fake_helper,
    ):
        await shadow_trade_monitor._capture_exit_features(db=None, shadow=shadow)

    assert shadow.features_snapshot_exit == full_snap
    # ML contract: all values scalar
    for v in shadow.features_snapshot_exit.values():
        assert isinstance(v, (int, float, bool)) or v is None


@pytest.mark.asyncio
async def test_capture_exit_features_writes_marker_when_empty():
    """Helper devolveu vazio → grava marcador para a UI distinguir."""
    from app.tasks import shadow_trade_monitor

    shadow = SimpleNamespace(
        id=uuid4(), symbol="ABC_USDT", features_snapshot_exit=None,
    )

    async def _fake_helper(db, symbol, **kw):
        return {}

    with patch.object(
        shadow_trade_monitor.indicators_provider,
        "build_full_flat_snapshot",
        new=_fake_helper,
    ):
        await shadow_trade_monitor._capture_exit_features(db=None, shadow=shadow)

    assert shadow.features_snapshot_exit == {
        "_capture_failed": True,
        "_reason": "indicators_unavailable_at_close",
    }


@pytest.mark.asyncio
async def test_capture_exit_features_writes_marker_on_provider_exception():
    """Task #312 — exceção do provider NUNCA sobe; helper grava marcador.

    Antes (Task #306): a exceção subia para o caller, que tinha try/except
    em volta — mas qualquer atribuição feita ANTES da exceção era perdida
    e ``features_snapshot_exit`` ficava NULL. A UI então mostrava
    "fechado antes da Task #306" mesmo para trades recentes (hipótese 1
    do task #312).

    Agora: o helper engole a exceção e grava
    ``{"_capture_failed": True, "_reason": "capture_exception",
    "_error": "<tipo>"}`` para a UI sinalizar regressão imediata.
    """
    from app.tasks import shadow_trade_monitor

    shadow = SimpleNamespace(
        id=uuid4(), symbol="ABC_USDT", features_snapshot_exit=None,
    )

    async def _boom(db, symbol, **kw):
        raise RuntimeError("DB down")

    with patch.object(
        shadow_trade_monitor.indicators_provider,
        "build_full_flat_snapshot",
        new=_boom,
    ):
        # Não levanta — invariante Task #312.
        await shadow_trade_monitor._capture_exit_features(
            db=None, shadow=shadow
        )

    assert shadow.features_snapshot_exit == {
        "_capture_failed": True,
        "_reason": "capture_exception",
        "_error": "RuntimeError: DB down",
    }


# ── record_as_simulation: persistência do exit snapshot ──────────────────────


class _StubResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _CapturingSession:
    """Captura o último ``execute(sql, params)`` para asserts no payload."""

    def __init__(self, returned_id=None):
        self.last_params = None
        self._returned_id = returned_id

    async def execute(self, sql, params):
        self.last_params = params
        return _StubResult((self._returned_id,) if self._returned_id else None)


def _make_shadow_completed(*, entry_snap, exit_snap):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        symbol="BTC_USDT",
        source="L3",
        outcome="TP_HIT",
        entry_price=100.0,
        tp_price=110.0,
        sl_price=90.0,
        exit_price=110.0,
        entry_timestamp=now,
        exit_timestamp=now,
        holding_seconds=600,
        decision_id=uuid4(),
        pnl_pct=None,
        features_snapshot=entry_snap,
        features_snapshot_exit=exit_snap,
        config_snapshot={"tp": 0.10, "sl": 0.10},
        mae_at=None,
        mfe_at=None,
        barrier_touched=None,
        barrier_touched_at=None,
        intrabar_convention=None,
        final_return_pct=None,
        net_return_pct=None,
        fee_roundtrip_pct_applied=None,
        barrier_mode=None,
        tp_pct_applied=None,
        sl_pct_applied=None,
        atr_pct_at_entry=None,
        min_price_post_entry=None,
        max_price_post_entry=None,
        max_drawdown_pct=None,
        max_profit_pct=None,
        mae_pct=None,
        mfe_pct=None,
        exit_metrics_json=None,
    )


@pytest.mark.asyncio
async def test_record_as_simulation_uses_exit_snapshot():
    """Quando ``features_snapshot_exit`` está completo, é ELE que vai pra
    ``trade_simulations.features_snapshot`` (não o snapshot de entrada)."""
    import json
    from app.services import shadow_trade_service

    entry_snap = {"rsi": 30.0, "adx": 18.0}
    exit_snap = {
        "rsi": 70.0, "adx": 35.0, "macd_histogram": 0.05,
        "taker_ratio": 0.8, "volume_delta": 2500.0, "ema9": 1.5,
    }
    shadow = _make_shadow_completed(entry_snap=entry_snap, exit_snap=exit_snap)
    session = _CapturingSession(returned_id=uuid4())

    await shadow_trade_service.record_as_simulation(session, shadow)

    assert session.last_params is not None
    persisted = json.loads(session.last_params["features_snapshot"])
    assert persisted == exit_snap
    # NÃO é o snapshot de entrada
    assert persisted != entry_snap
    # Contrato flat: todos os valores são escalares.
    for v in persisted.values():
        assert isinstance(v, (int, float, bool)) or v is None


@pytest.mark.asyncio
async def test_record_as_simulation_falls_back_to_entry_on_capture_failure():
    """Marcador ``_capture_failed`` no exit → fallback para entrada."""
    import json
    from app.services import shadow_trade_service

    entry_snap = {"rsi": 30.0, "adx": 18.0}
    exit_marker = {
        "_capture_failed": True,
        "_reason": "indicators_unavailable_at_close",
    }
    shadow = _make_shadow_completed(entry_snap=entry_snap, exit_snap=exit_marker)
    session = _CapturingSession(returned_id=uuid4())

    await shadow_trade_service.record_as_simulation(session, shadow)

    persisted = json.loads(session.last_params["features_snapshot"])
    assert persisted == entry_snap
    # Marcador NUNCA vaza para trade_simulations (quebraria DatasetBuilder).
    assert "_capture_failed" not in persisted


@pytest.mark.asyncio
async def test_record_as_simulation_falls_back_to_entry_when_exit_null():
    """``features_snapshot_exit`` NULL (trade antigo) → fallback para entrada."""
    import json
    from app.services import shadow_trade_service

    entry_snap = {"rsi": 30.0, "adx": 18.0}
    shadow = _make_shadow_completed(entry_snap=entry_snap, exit_snap=None)
    session = _CapturingSession(returned_id=uuid4())

    await shadow_trade_service.record_as_simulation(session, shadow)

    persisted = json.loads(session.last_params["features_snapshot"])
    assert persisted == entry_snap


# ── Integration: _record_simulation_one_async swallows capture failure ───────


@pytest.mark.asyncio
async def test_record_simulation_one_async_swallows_capture_failure():
    """FIX D1: falha no `_capture_exit_features` NUNCA aborta gravação da
    simulação nem o fechamento do shadow (best-effort).

    Cobre o invariante combinado:
      1. Exceção do provider sobe de `_capture_exit_features` (caso 3 acima).
      2. Caller `_record_simulation_one_async` engole no try/except e ainda
         chama `record_as_simulation` com o shadow recarregado.
    """
    from app.tasks import shadow_trade_monitor

    shadow = _make_shadow_completed(
        entry_snap={"rsi": 30.0}, exit_snap=None,
    )
    shadow.status = "COMPLETED"

    # Stub minimalista: db.execute(select…) → scalar_one_or_none() == shadow.
    class _SelectResult:
        def scalar_one_or_none(self_inner):
            return shadow

    class _TxCtx:
        async def __aenter__(self_inner):
            return None
        async def __aexit__(self_inner, *a):
            return False

    class _StubDb:
        def begin(self_inner):
            return _TxCtx()
        async def execute(self_inner, _stmt):
            return _SelectResult()

    class _SessionCtx:
        async def __aenter__(self_inner):
            return _StubDb()
        async def __aexit__(self_inner, *a):
            return False

    def _SessionFactory():
        return _SessionCtx()

    record_calls = []

    async def _fake_record(db, sh):
        record_calls.append(sh.id)
        return uuid4()

    async def _capture_boom(db, sh):
        raise RuntimeError("provider down at close")

    # CRÍTICO: `_record_simulation_one_async` faz `from ..database import
    # CeleryAsyncSessionLocal` em runtime, então temos que patchar no módulo
    # de origem (`app.database`), não em `shadow_trade_monitor`.
    from app import database as app_database

    with patch.object(
        app_database, "CeleryAsyncSessionLocal", _SessionFactory,
    ), patch.object(
        shadow_trade_monitor, "_capture_exit_features", new=_capture_boom,
    ), patch.object(
        shadow_trade_monitor.shadow_trade_service,
        "record_as_simulation",
        new=_fake_record,
    ):
        # Não deve levantar — invariante D1.
        await shadow_trade_monitor._record_simulation_one_async(shadow.id)

    # Apesar da falha do capture, a simulação foi gravada (best-effort).
    assert record_calls == [shadow.id]
