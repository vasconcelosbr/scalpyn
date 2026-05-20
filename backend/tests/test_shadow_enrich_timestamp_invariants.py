"""Invariantes contra storm `timestamptz <= interval` no Shadow enrichment.

Background (Task #309)
----------------------
Em 2026-05-19/20 voltou no Cloud SQL log o erro:

    operator does not exist: timestamp with time zone <= interval at character 183

Cascateando 3 erros raiz + 9 ``current transaction is aborted`` por
ciclo do ``shadow_trade_monitor``. A causa: asyncpg encoda
``datetime.timedelta`` Python como Postgres ``INTERVAL``. Quando algum
produtor passa um valor não-``datetime`` (timedelta, ``None``, ``str``,
``int``) como parâmetro bound em queries do tipo ``time <= :t``, a
comparação vira ``timestamptz <= interval`` e a transação inteira
aborta.

A defesa (Task #309): ``shadow_trade_service.enrich_market_context``
valida CADA parâmetro temporal bound (entry_timestamp, t_anchor) antes
de mandar pra ``db.execute`` — em caso de tipo inválido, aborta APENAS
o bloco afetado, preservando enriquecimento parcial dos demais campos.

Estes testes garantem:

1. ``enrich_market_context`` não levanta exceção quando recebe
   ``timedelta``, ``None``, ``str`` ou ``int`` como ``entry_timestamp``
   — retorna dict com 4 ``None``s.
2. Quando entry_timestamp é inválido, NENHUMA query SQL é executada
   (defesa em depth — não dependemos do Postgres reportar o erro).
3. Quando ``cur_row.time`` (vindo do BTC OHLCV) é não-``datetime``, a
   segunda query (`:t_anchor`) NÃO é executada, mas a primeira ainda
   popula ``btc_price_at_entry``.
4. Lint: todo callsite em SQL raw do shadow_trade_service que faz
   ``time <= :param`` / ``time >= :param`` está coberto pelo guard
   ``_validate_temporal_param`` no caminho do parâmetro.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from app.services import shadow_trade_service


# ──────────────────────────────────────────────────────────────────────
# Fakes assíncronos mínimos (sem postgres real).
# ──────────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, row: Optional[Any]) -> None:
        self._row = row

    def fetchone(self) -> Optional[Any]:
        return self._row


class _FakeAsyncSession:
    """Substituto de AsyncSession que registra parâmetros recebidos.

    Cada chamada a ``execute(stmt, params)`` grava a tupla em
    ``self.calls`` e devolve a próxima ``_FakeResult`` da fila
    ``self.responses`` (ou None-row se a fila acabar).
    """

    def __init__(self, responses: Optional[List[Optional[Any]]] = None) -> None:
        self.calls: List[tuple[str, dict]] = []
        self.responses: List[Optional[Any]] = list(responses or [])

    async def execute(self, stmt, params=None):
        sql = str(stmt) if stmt is not None else ""
        self.calls.append((sql, dict(params or {})))
        row = self.responses.pop(0) if self.responses else None
        return _FakeResult(row)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.new_event_loop().run_until_complete(coro)


# ──────────────────────────────────────────────────────────────────────
# Testes funcionais.
# ──────────────────────────────────────────────────────────────────────

_BAD_TIMESTAMPS = [
    pytest.param(timedelta(seconds=42), id="timedelta"),
    pytest.param("2026-05-20T00:00:00Z", id="str"),
    pytest.param(1_716_000_000, id="int"),
    pytest.param(1_716_000_000.5, id="float"),
    pytest.param(object(), id="object"),
]


@pytest.mark.parametrize("bad_value", _BAD_TIMESTAMPS)
def test_enrich_aborts_on_non_datetime_entry_timestamp(bad_value):
    """Tipo inválido em entry_timestamp NÃO chega ao SQL e NÃO levanta."""
    db = _FakeAsyncSession()
    out = _run(
        shadow_trade_service.enrich_market_context(
            db,
            symbol="BTC_USDT",
            entry_timestamp=bad_value,
            decision_id=12345,
        )
    )
    assert out == {
        "btc_price_at_entry": None,
        "btc_change_1h_pct": None,
        "funding_rate_at_entry": None,
        "n_concurrent_signals": None,
    }
    # Defesa em depth: o guard cortou ANTES de chamar o banco.
    assert db.calls == [], (
        f"enrich_market_context emitiu SQL com entry_timestamp={bad_value!r} "
        f"(type={type(bad_value).__name__}); guard deveria ter abortado antes."
    )


def test_enrich_returns_empty_dict_when_entry_timestamp_is_none():
    """``None`` é o caso comum (shadow PENDING sem entrada resolvida)."""
    db = _FakeAsyncSession()
    out = _run(
        shadow_trade_service.enrich_market_context(
            db,
            symbol="BTC_USDT",
            entry_timestamp=None,
            decision_id=None,
        )
    )
    assert out["btc_price_at_entry"] is None
    assert out["funding_rate_at_entry"] is None
    assert out["n_concurrent_signals"] is None
    assert db.calls == []


def test_enrich_skips_t_anchor_block_when_cur_row_time_invalid():
    """Se BTC ohlcv devolver ``cur_row.time`` não-``datetime``, a query
    ``:t_anchor`` NÃO é emitida (storm-prevention), mas
    ``btc_price_at_entry`` ainda é populado a partir do ``close``.
    """
    # Primeira query (BTC ohlcv corrente) devolve close válido mas
    # `time` corrompido (timedelta). Segunda query (`:t_anchor`) NÃO
    # deve acontecer. Terceira é funding_rates (None row).
    # Quarta é concurrent signals (n=0).
    db = _FakeAsyncSession(
        responses=[
            SimpleNamespace(close=30000.0, time=timedelta(seconds=1)),
            SimpleNamespace(rate=None),
            SimpleNamespace(n=0),
        ]
    )
    out = _run(
        shadow_trade_service.enrich_market_context(
            db,
            symbol="BTC_USDT",
            entry_timestamp=datetime(2026, 5, 20, tzinfo=timezone.utc),
            decision_id=999,
        )
    )
    # BTC price foi capturado da primeira query.
    assert out["btc_price_at_entry"] == 30000.0
    # 1h change foi pulado porque t_anchor era inválido.
    assert out["btc_change_1h_pct"] is None
    # Funding e concurrent foram tentados normalmente.
    assert out["funding_rate_at_entry"] is None
    assert out["n_concurrent_signals"] == 0

    # Exatamente 3 queries (BTC + funding + concurrent), NÃO 4.
    assert len(db.calls) == 3, (
        f"Esperava 3 queries (BTC, funding, concurrent — t_anchor pulado), "
        f"mas houve {len(db.calls)}: {[c[0][:60] for c in db.calls]}"
    )
    # Nenhuma das queries emitidas deve carregar timedelta como parâmetro.
    for sql, params in db.calls:
        for k, v in params.items():
            assert not isinstance(v, timedelta), (
                f"Parâmetro {k}={v!r} é timedelta — reintroduziria o storm 2026-05-19."
            )


def test_enrich_continues_funding_and_concurrent_when_btc_block_fails():
    """Mesmo se BTC retornar None, funding e concurrent rodam."""
    db = _FakeAsyncSession(
        responses=[
            None,  # BTC ohlcv: nenhum row
            SimpleNamespace(rate=0.0001),
            SimpleNamespace(n=7),
        ]
    )
    out = _run(
        shadow_trade_service.enrich_market_context(
            db,
            symbol="ETH_USDT",
            entry_timestamp=datetime(2026, 5, 20, tzinfo=timezone.utc),
            decision_id=1,
        )
    )
    assert out["btc_price_at_entry"] is None
    assert out["btc_change_1h_pct"] is None
    assert out["funding_rate_at_entry"] == pytest.approx(0.0001)
    assert out["n_concurrent_signals"] == 7


# ──────────────────────────────────────────────────────────────────────
# Lint test: produtores de SQL raw com `time <= :param` no shadow service
# DEVEM ter o guard _validate_temporal_param no caminho.
# ──────────────────────────────────────────────────────────────────────

_SHADOW_SERVICE = Path(__file__).resolve().parent.parent / "app" / "services" / "shadow_trade_service.py"


def test_shadow_service_guards_all_temporal_bind_params():
    """Toda query raw em shadow_trade_service com ``time <= :x`` /
    ``time >= :x`` deve ter um sibling ``_validate_temporal_param`` no
    mesmo módulo. Lint defensivo (Task #309)."""
    src = _SHADOW_SERVICE.read_text(encoding="utf-8")
    # Padrão alvo: `time <= :ident` ou `time >= :ident`.
    patterns = re.findall(r"time\s*[<>]=?\s*:\w+", src)
    assert patterns, (
        "Nenhum 'time <= :param' encontrado — refatoração mudou o shape "
        "do enrichment? Atualizar este teste."
    )
    assert "_validate_temporal_param" in src, (
        "Helper _validate_temporal_param não está presente em "
        "shadow_trade_service.py — guard contra storm 2026-05-19 removido. "
        "Toda query raw com 'time <= :param' precisa do guard."
    )
    # Pelo menos 2 chamadas (entry_timestamp + t_anchor).
    n_calls = src.count("_validate_temporal_param(")
    assert n_calls >= 3, (  # 1 definição + 2 chamadas
        f"Esperava ≥3 ocorrências de _validate_temporal_param (def + entry_ts + "
        f"t_anchor), achou {n_calls}. Não relaxar sem reintroduzir guard "
        f"equivalente em cada bind temporal — ver Task #309."
    )
