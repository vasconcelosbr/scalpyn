"""Cobertura da cascata de fallback do Shadow Portfolio (Task #303).

Cenários:

* ``test_flatten_analysis_snapshot_basic`` — confirma que o helper que
  achata ``pipeline_watchlist_assets.analysis_snapshot`` aceita o formato
  produzido pelo ``pipeline_scan`` e descarta entradas inválidas
  (dict/list em ``value``) mantendo o contrato flat da Task #290.

* ``test_resolve_with_fallback_falls_through_to_live_l3`` — quando não
  existe ``DecisionLog`` para (user, symbol), o resolver constrói uma
  ``_SyntheticDecision`` a partir do snapshot vivo, com
  ``decision.id=None`` e ``direction='SPOT'``. Garante que o ``source``
  retornado seja ``"live_l3"`` (Prometheus key).

* ``test_resolve_with_fallback_prefers_recent_log_over_snapshot`` — se
  existe uma decisão recente em log, ela é usada e o snapshot é ignorado
  (source=``recent_log``). Cobre a regressão "shadow duplicado quando o
  log e o snapshot apontam para o mesmo símbolo".

Testes são puros (sem DB real): usam uma sessão dublê que devolve listas
controladas para os dois ``select(DecisionLog)`` do resolver.
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.shadow_trade_service import (  # noqa: E402
    _SyntheticDecision,
    _flatten_analysis_snapshot,
    _resolve_decision_with_fallback,
)


# ── Test doubles ─────────────────────────────────────────────────────────────


@dataclass
class _StubResult:
    """Imitates the small subset of ``Result`` we rely on."""
    rows: List[Any]

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _StubSession:
    """Pre-seeded results queue — one per ``execute`` call in order."""
    def __init__(self, results: List[_StubResult]):
        self._results = list(results)
        self.calls = 0

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        if not self._results:
            return _StubResult([])
        return self._results.pop(0)


# ── flatten_analysis_snapshot ────────────────────────────────────────────────


def test_flatten_analysis_snapshot_basic():
    snap = {
        "indicators": [
            {"key": "rsi", "value": 42.5},
            {"key": "adx", "value": 18},
            # Discarded: missing key
            {"value": 1.0},
            # Discarded: dict value would break DatasetBuilder
            {"key": "macd", "value": {"hist": 0.1}},
            # Preserved as None
            {"key": "obv", "value": None},
        ]
    }
    flat = _flatten_analysis_snapshot(snap)
    assert flat == {"rsi": 42.5, "adx": 18, "obv": None}


def test_flatten_analysis_snapshot_handles_details_wrapper():
    snap = {
        "details": {
            "indicators": [{"key": "ema9", "value": 1.23}]
        }
    }
    assert _flatten_analysis_snapshot(snap) == {"ema9": 1.23}


def test_flatten_analysis_snapshot_returns_empty_for_non_dict():
    assert _flatten_analysis_snapshot(None) == {}
    assert _flatten_analysis_snapshot([]) == {}
    assert _flatten_analysis_snapshot({"indicators": "garbage"}) == {}


# ── _resolve_decision_with_fallback ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_with_fallback_falls_through_to_live_l3():
    """Sem decisão em log → cai para o snapshot vivo com source='live_l3'."""
    user_id = uuid4()
    snap_item = {
        "symbol": "BTC_USDT",
        "score": 58.0,
        "direction": "SPOT",
        "approved_at": datetime.now(timezone.utc),
        "watchlist_id": uuid4(),
        "watchlist_name": "L3 Spot",
        "indicators_snapshot": {"rsi": 55.0, "adx": 22.0},
    }
    # Duas queries (recent + stale) retornam vazio.
    session = _StubSession([_StubResult([]), _StubResult([])])
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)

    decision, source = await _resolve_decision_with_fallback(
        session, user_id, "BTC_USDT", snap_item, cutoff
    )

    assert source == "live_l3"
    assert isinstance(decision, _SyntheticDecision)
    assert decision.id is None  # CRÍTICO: vira NULL no shadow_trades.decision_id
    assert decision.direction == "SPOT"
    assert decision.symbol == "BTC_USDT"
    # Metrics envelopa indicadores no formato que _build_features_snapshot
    # achata corretamente (contrato Task #290).
    assert decision.metrics["indicators_snapshot"]["rsi"] == {"value": 55.0}


@pytest.mark.asyncio
async def test_resolve_with_fallback_prefers_recent_log_over_snapshot():
    """Decisão recente vence o snapshot — sem duplicar."""
    user_id = uuid4()

    @dataclass
    class _FakeDecision:
        id: int
        user_id: Any
        symbol: str
        direction: str = "SPOT"
        strategy: str = None
        created_at: datetime = None
        metrics: dict = None

    fake = _FakeDecision(
        id=12345, user_id=user_id, symbol="ETH_USDT",
        created_at=datetime.now(timezone.utc), metrics={},
    )
    snap_item = {
        "symbol": "ETH_USDT", "score": 60.0, "direction": "SPOT",
        "approved_at": None, "watchlist_id": uuid4(),
        "watchlist_name": "L3", "indicators_snapshot": {"rsi": 50.0},
    }
    session = _StubSession([_StubResult([fake])])
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)

    decision, source = await _resolve_decision_with_fallback(
        session, user_id, "ETH_USDT", snap_item, cutoff
    )

    assert source == "recent_log"
    assert decision is fake
    # Só a primeira query (recent) deve ter sido feita — sem fallback.
    assert session.calls == 1
