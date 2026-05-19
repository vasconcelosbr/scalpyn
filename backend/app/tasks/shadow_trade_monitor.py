"""Shadow Trade Monitor — Fase 3.

Avança shadow trades em PENDING/RUNNING candle-a-candle (1m) até
atingir TP, SL ou timeout, e replica o resultado em
``trade_simulations`` com ``source='SHADOW'`` (alimenta
``DatasetBuilder.load_simulations``).

Regras
------
* **SL antes de TP** na mesma candle (regra conservadora explícita).
  Quando ``low <= sl_price`` E ``high >= tp_price`` na mesma candle,
  assumimos SL_HIT — pior caso para o trader.
* Timeout: ``timeout_candles`` vem de ``config_snapshot``
  (``SHADOW_TIMEOUT_CANDLES`` env quando ausente).
* Batch máximo ``SHADOW_MONITOR_BATCH_SIZE`` (default 50) por execução,
  iterando IDs em ordem ``sorted()`` (deadlock-safety, gotcha #251/#273).
* Janela máxima por shadow ``SHADOW_MONITOR_MAX_CANDLES_PER_RUN``
  (default 720 = 12 h de 1m) — evita um shadow muito antigo monopolizar
  o ciclo. Se passar disso sem outcome, atualiza ``last_processed_time``
  e o próximo tick continua.
* ``acks_late=False`` (idempotente, beat re-roda; gotcha #245).

Knobs (env)
-----------
* ``SHADOW_MONITOR_INTERVAL_S`` (default 300) — beat schedule.
* ``SHADOW_MONITOR_BATCH_SIZE`` (default 50).
* ``SHADOW_MONITOR_MAX_CANDLES_PER_RUN`` (default 720).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from .celery_app import celery_app
from ..models.shadow_trade import ShadowTrade
from ..services import indicators_provider, shadow_trade_service

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SHADOW_MONITOR_BATCH_SIZE = _env_int("SHADOW_MONITOR_BATCH_SIZE", 50)
SHADOW_MONITOR_MAX_CANDLES_PER_RUN = _env_int("SHADOW_MONITOR_MAX_CANDLES_PER_RUN", 720)


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Task #274 — canonical 5-step teardown. See collect_market_data._run_async
    for the full rationale. Steps: cancel pending tasks → dispose engine →
    hard-terminate asyncpg connections → shutdown_asyncgens → close loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Step 1 — cancel and drain pending asyncio tasks.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:
            logger.debug("[_run_async] pending-task drain failed: %s", exc)

        # Step 2 — graceful engine dispose (closes asyncpg sockets in-loop).
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            # Step 2b (Task #300 review) — drain microtasks scheduled
            # during dispose() (asyncpg finalizers) before hard-terminate
            # so half-released sockets don't re-arm GC callbacks on a
            # loop we're about to close.
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[_run_async] _celery_engine.dispose failed: %s", exc)

        # Step 3 — hard-terminate any asyncpg connection still cached on the pool.
        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                asyncpg_conn = (
                    getattr(raw, "_connection", None)
                    or getattr(raw, "connection", None)
                    or raw
                )
                terminate = getattr(asyncpg_conn, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except BaseException:
                        pass
        except BaseException as exc:
            logger.debug("[_run_async] hard-terminate sweep failed: %s", exc)

        # Step 4 — drain async generators registered on the loop.
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[_run_async] shutdown_asyncgens failed: %s", exc)

        # Step 5 — close the loop. Always last; never propagate.
        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[_run_async] loop.close failed: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _fetch_candles(
    db, symbol: str, after_ts: datetime, limit: int
) -> List[Dict[str, Any]]:
    """1m OHLCV candles após ``after_ts`` (exclusive), em ordem temporal."""
    res = await db.execute(
        text(
            """
            SELECT time, open, high, low, close
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time > :t
             ORDER BY time ASC
             LIMIT :lim
            """
        ),
        {"s": symbol, "t": after_ts, "lim": limit},
    )
    return [
        {
            "time": r.time,
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
        }
        for r in res.fetchall()
    ]


async def _ensure_entry(db, shadow: ShadowTrade) -> bool:
    """Garante que o shadow tem ``entry_price`` + ``entry_timestamp``.

    Novo fluxo (Task 2026-05-13): a criação já tenta preencher entry
    com o preço CORRENTE multi-timeframe (1m/5m/15m/30m), então a
    grande maioria dos shadows nasce em RUNNING. Esse helper cobre:

    * shadows legados criados antes da migration (status PENDING +
      entry NULL — backfill on-the-fly aqui);
    * raros casos onde nem candle 1m, nem 5m/15m/30m estavam
      disponíveis no instante da decisão.

    Estratégia: tenta primeiro o preço corrente multi-tf (mesmo helper
    usado pelo creator). Só recai pro ``_next_1m_open`` legado se nada
    disponível — mantendo o contrato antigo como último recurso.
    """
    if shadow.entry_price is not None and shadow.entry_timestamp is not None:
        return True

    entry_price, entry_ts = await shadow_trade_service._get_current_price_multi_tf(
        db, shadow.symbol
    )
    if entry_price is None or entry_ts is None:
        entry_price, entry_ts = await shadow_trade_service._next_1m_open(
            db, shadow.symbol, shadow.created_at
        )
    if entry_price is None or entry_ts is None:
        return False

    shadow.entry_price = entry_price
    # Guard contra produtor errôneo (storm Cloud SQL 2026-05-19): se
    # algum helper retornar entry_ts não-datetime, o enrich_market_context
    # subsequente vai gerar `timestamp with time zone <= interval` em prod.
    # Logamos e abortamos a entrada — shadow fica PENDING e tenta no próximo ciclo.
    if not isinstance(entry_ts, datetime):
        logger.error(
            "[shadow-monitor] _ensure_entry: entry_ts não-datetime "
            "(type=%s value=%r shadow_id=%s symbol=%s) — abortando entrada",
            type(entry_ts).__name__, entry_ts, shadow.id, shadow.symbol,
        )
        return False
    shadow.entry_timestamp = entry_ts
    # Promove legado a RUNNING assim que a entrada é resolvida — caso
    # contrário, se não houver candles 1m novas neste tick, o trade
    # ficaria PENDING mesmo já tendo entry_price (ressalva do review).
    if shadow.status == "PENDING":
        shadow.status = "RUNNING"
    if shadow.last_processed_time is None:
        shadow.last_processed_time = entry_ts
    if (
        shadow.tp_pct is not None
        and shadow.sl_pct is not None
        and entry_price > 0
    ):
        shadow.tp_price = entry_price * (1 + float(shadow.tp_pct) / 100.0)
        shadow.sl_price = entry_price * (1 - float(shadow.sl_pct) / 100.0)
    return True


def _finalize_outcome(
    shadow: ShadowTrade,
    outcome: str,
    exit_price: Optional[float],
    exit_ts: Optional[datetime],
    entry_price: float,
) -> None:
    """Aplica os campos finais do shadow ao bater outcome (TP/SL/TIMEOUT).

    Refatoração 2026-05-14: extraído do bloco inline de ``_advance_shadow``
    para que o caminho live-close (introduzido nesta data) reuse a mesma
    lógica de PnL/holding/COMPLETED sem duplicar. Comportamento
    idêntico ao bloco legado — pure refactor.
    """
    shadow.outcome = outcome
    shadow.exit_price = exit_price
    shadow.exit_timestamp = exit_ts
    if entry_price > 0 and exit_price is not None:
        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
        shadow.pnl_pct = pnl_pct
        shadow.pnl_usdt = float(shadow.amount_usdt) * pnl_pct / 100.0
    if shadow.entry_timestamp and exit_ts:
        shadow.holding_seconds = int(
            (exit_ts - shadow.entry_timestamp).total_seconds()
        )
    shadow.status = "COMPLETED"
    shadow.completed_at = datetime.now(timezone.utc)
    shadow.last_processed_time = exit_ts


async def _capture_exit_features(db, shadow: ShadowTrade) -> None:
    """Preenche ``features_snapshot_exit`` com o snapshot completo de
    indicadores na saída (Task #306).

    Usa o helper canônico ``indicators_provider.build_full_flat_snapshot``
    — single source of truth do contrato flat ``{key: scalar}`` — para
    capturar o MESMO conjunto de chaves que aparece no snapshot de
    entrada (gravado por ``decisions_log.metrics["indicators_snapshot"]``).
    Isso alimenta o XGBoost com "entrada vs saída" simétricos
    (Task #290: contrato flat preservado).

    Caller DEVE envolver em try/except — falha aqui não pode anular o
    outcome (invariante D1: TP/SL/timeout sempre vencem).

    Quando o provider devolve vazio (símbolo sem indicadores merged no
    instante do fechamento — Redis fora, indicador stale fora da janela
    aceita, etc.), gravamos um marcador ``{"_capture_failed": True,
    "_reason": "indicators_unavailable_at_close"}`` para que o frontend
    consiga distinguir "snapshot indisponível" de "ainda não capturado"
    (NULL) e renderizar uma mensagem informativa em vez de "Sem dados".
    """
    snapshot = await indicators_provider.build_full_flat_snapshot(
        db, shadow.symbol, include_stale=True
    )
    if snapshot:
        shadow.features_snapshot_exit = snapshot
    else:
        logger.warning(
            "[shadow-monitor] _capture_exit_features: empty snapshot for "
            "shadow_id=%s symbol=%s — provider returned no merged indicators",
            shadow.id, shadow.symbol,
        )
        shadow.features_snapshot_exit = {
            "_capture_failed": True,
            "_reason": "indicators_unavailable_at_close",
        }


async def _enrich_market_context(db, shadow: ShadowTrade) -> None:
    """Preenche os 4 campos de contexto de mercado (migration 052).

    Idempotente: só chama o serviço se ALGUM dos 4 campos ainda for NULL
    (uma vez preenchidos no momento da entrada, são imutáveis — o
    contexto é "como era o mercado quando o trade entrou", não muda
    com o tempo). Defesa adicional: nunca propaga exceção, perda do
    enriquecimento não afeta TP/SL/timeout.
    """
    needs_fill = (
        shadow.btc_price_at_entry is None
        or shadow.btc_change_1h_pct is None
        or shadow.funding_rate_at_entry is None
        or shadow.n_concurrent_signals is None
    )
    if not needs_fill or shadow.entry_timestamp is None:
        return
    try:
        ctx = await shadow_trade_service.enrich_market_context(
            db,
            symbol=shadow.symbol,
            entry_timestamp=shadow.entry_timestamp,
            decision_id=shadow.decision_id,
        )
        if shadow.btc_price_at_entry is None and ctx["btc_price_at_entry"] is not None:
            shadow.btc_price_at_entry = ctx["btc_price_at_entry"]
        if shadow.btc_change_1h_pct is None and ctx["btc_change_1h_pct"] is not None:
            shadow.btc_change_1h_pct = ctx["btc_change_1h_pct"]
        if shadow.funding_rate_at_entry is None and ctx["funding_rate_at_entry"] is not None:
            shadow.funding_rate_at_entry = ctx["funding_rate_at_entry"]
        if shadow.n_concurrent_signals is None and ctx["n_concurrent_signals"] is not None:
            shadow.n_concurrent_signals = ctx["n_concurrent_signals"]
    except Exception:
        logger.exception(
            "[shadow-monitor] enrich_market_context failed for shadow_id=%s "
            "— continuing without context (TP/SL/timeout untouched)",
            shadow.id,
        )


async def _enrich_one_async(
    shadow_id: Any,
    symbol: str,
    entry_timestamp: datetime,
    decision_id: Optional[int],
) -> None:
    """Enriquece campos de contexto ML de um shadow em sessão isolada.

    FIX C3 (2026-05-15): enriquecimento movido para fora da tx principal do
    monitor. Qualquer SQL error dentro de ``enrich_market_context`` (ex:
    coluna ausente, lock, timeout) agora aborta apenas ``db_enrich`` — a
    sessão principal já commitou os fechamentos e está encerrada.
    Falha é logada como WARNING e ignorada (best-effort por design).
    """
    from ..database import CeleryAsyncSessionLocal

    try:
        async with CeleryAsyncSessionLocal() as db_enrich:
            async with db_enrich.begin():
                res = await db_enrich.execute(
                    select(ShadowTrade).where(ShadowTrade.id == shadow_id)
                )
                shadow = res.scalar_one_or_none()
                if shadow is None:
                    return
                # Reutiliza o wrapper já fail-safe internamente.
                await _enrich_market_context(db_enrich, shadow)
    except Exception:
        logger.warning(
            "[shadow-monitor] _enrich_one_async falhou para shadow_id=%s "
            "— ignorando (best-effort, fechamentos não afetados)",
            shadow_id,
        )


async def _record_simulation_one_async(shadow_id: Any) -> None:
    """Grava simulação de um shadow COMPLETED em sessão isolada.

    FIX D1 (2026-05-15): record_as_simulation + _capture_exit_features
    movidos para FORA da tx principal do monitor, seguindo EXATAMENTE o
    padrão de _enrich_one_async (FIX C3). Qualquer SQL error (ex: coluna
    ausente em trade_simulations, lock timeout, schema drift) agora aborta
    apenas db_sim — a sessão principal já commitou shadow.status='COMPLETED'
    e está encerrada. Falha é logada como WARNING e ignorada (best-effort).

    Invariante: recarrega o shadow por ID e verifica status == 'COMPLETED'
    antes de gravar — garante idempotência e protege contra rollback externo.
    """
    from ..database import CeleryAsyncSessionLocal

    try:
        async with CeleryAsyncSessionLocal() as db_sim:
            async with db_sim.begin():
                res = await db_sim.execute(
                    select(ShadowTrade).where(ShadowTrade.id == shadow_id)
                )
                shadow = res.scalar_one_or_none()
                if shadow is None:
                    return
                if shadow.status != "COMPLETED":
                    # Shadow foi revertido por outra tx — não gravar simulação
                    # órfã. Não logar como erro: é comportamento esperado em
                    # cenário de concorrência (FOR UPDATE SKIP LOCKED garante
                    # exclusividade mas não impede revert manual/admin).
                    return
                try:
                    await _capture_exit_features(db_sim, shadow)
                except Exception:
                    logger.exception(
                        "[shadow-monitor] features_snapshot_exit failed for "
                        "shadow_id=%s — simulação gravada sem exit snapshot",
                        shadow_id,
                    )
                await shadow_trade_service.record_as_simulation(db_sim, shadow)
    except Exception:
        logger.warning(
            "[shadow-monitor] _record_simulation_one_async falhou para "
            "shadow_id=%s — ignorando (best-effort, fechamento não afetado)",
            shadow_id,
        )


async def _advance_shadow(db, shadow: ShadowTrade) -> str:
    """Avança um único shadow trade até outcome ou esgotar candles do tick.

    Retorna um label de transição: ``"completed"``, ``"running"`` ou
    ``"pending"``.
    """
    if not await _ensure_entry(db, shadow):
        # Sem candle 1m disponível ainda — deixa em PENDING, próximo tick.
        return "pending"

    # NOTA: enriquecimento de contexto ML (_enrich_market_context) foi movido
    # para FORA da tx principal — ver _enrich_one_async + _monitor_async.
    # FIX C3 (2026-05-15): SQL error no enrich não deve abortar fechamentos.

    # Sem TP/SL utilizáveis (config sem tp_pct/sl_pct) → não dá pra simular.
    if shadow.tp_price is None or shadow.sl_price is None:
        logger.warning(
            "[shadow-monitor] shadow_id=%s sem tp/sl utilizáveis (tp_pct=%s "
            "sl_pct=%s) — marcado ERROR",
            shadow.id, shadow.tp_pct, shadow.sl_pct,
        )
        shadow.status = "ERROR"
        shadow.completed_at = datetime.now(timezone.utc)
        return "completed"

    tp = float(shadow.tp_price)
    sl = float(shadow.sl_price)
    entry_price = float(shadow.entry_price)

    # ── Live-close path (Task #292, 2026-05-14): muitos símbolos do
    # pool não têm OHLCV 1m ingerido — só 5m/15m/30m. O scan candle-a-
    # candle 1m abaixo nunca encontrava nada, e trades que VISIVELMENTE
    # já cruzaram o TP no preço atual ficavam parados em RUNNING (a UI
    # mostra current_price > tp_price mas o monitor não fechava).
    #
    # Fonte do preço: ``market_metadata.price`` (refresh ~60s via ticker
    # REST do Gate.io em ``collect_all``), NÃO ``ohlcv``. Iteração #1
    # desta task usou ``_get_current_price_multi_tf`` (close de candle
    # 5m/15m/30m), mas como ``_ensure_entry`` usa o MESMO helper, o
    # ``entry_price`` ficava igual ao ``live_price`` (mesma candle) e o
    # guard ``live_price != entry_price`` rejeitava o fechamento. Além
    # disso, o frontend lê ``market_metadata.price`` para mostrar o
    # current_price ao usuário — usar a mesma fonte garante coerência
    # visual: se a UI mostra preço acima do TP, o monitor fecha.
    #
    # Conservadorismo: se current_price <= sl, registramos SL_HIT
    # (mesma lógica do scan candle). exit_price é fixado em tp/sl
    # para manter o PnL simulado consistente com tp_pct/sl_pct
    # (o scan 1m existente também usa tp/sl, não c["high"]/c["low"]).
    # Lemos AS DUAS fontes de preço corrente e fechamos se QUALQUER uma
    # cruzou TP/SL. Por quê:
    #   - `market_metadata.price` = ticker REST (refresh ~60s).
    #   - `ohlcv` close multi-tf (1m/5m/15m/30m) = mesma fonte usada por
    #     `_fetch_latest_prices` no endpoint `/api/shadow-trades/prices`,
    #     que é o que o frontend mostra como "Atual" para o usuário.
    # As duas podem divergir (ticker passou pelo TP num minuto sem candle
    # fechada, ou ohlcv mais recente que ticker). Avaliar AS DUAS:
    #   - garante coerência visual (UI ≥ TP ⇒ fechamento)
    #   - é mais agressivo no fechamento (qualquer fonte que cruzou já
    #     prova que o preço passou por lá em algum momento)
    try:
        mm_price, mm_ts = await shadow_trade_service._get_market_metadata_price(
            db, shadow.symbol
        )
    except Exception:
        logger.exception(
            "[shadow-monitor] live-close: get_market_metadata_price failed "
            "for shadow_id=%s",
            shadow.id,
        )
        mm_price, mm_ts = None, None

    try:
        ohlcv_price, ohlcv_ts = await shadow_trade_service._get_current_price_multi_tf(
            db, shadow.symbol
        )
    except Exception:
        logger.exception(
            "[shadow-monitor] live-close: get_current_price_multi_tf failed "
            "for shadow_id=%s",
            shadow.id,
        )
        ohlcv_price, ohlcv_ts = None, None

    # Skew guard por fonte: descarta dado anterior ao entry_timestamp
    # (ticker/candle pré-entrada). Mantém a fonte cujo timestamp é
    # >= entry_ts (ou é NULL — sem como caracterizar skew, aceitamos).
    # FIX C2 (2026-05-15): try/except em torno da comparação de datas
    # para cobrir possível mismatch timezone-naive vs timezone-aware
    # (ex: market_metadata.last_updated gravado como TIMESTAMP sem tz
    # enquanto shadow.entry_timestamp é TIMESTAMPTZ). Se a comparação
    # lançar TypeError, aceita a fonte — melhor fechar trade válido com
    # preço ligeiramente impreciso do que nunca fechar (fail-open
    # intencional no skew guard; TP/SL check ainda valida o cruzamento).
    def _ok(price, ts) -> bool:
        if price is None:
            return False
        if ts is None or shadow.entry_timestamp is None:
            return True
        try:
            return ts >= shadow.entry_timestamp
        except TypeError:
            # naive vs aware — aceitar fonte válida (ver FIX C2 acima)
            return True

    candidates: list[tuple[float, Optional[datetime], str]] = []
    if _ok(mm_price, mm_ts):
        candidates.append((float(mm_price), mm_ts, "mm"))
    if _ok(ohlcv_price, ohlcv_ts):
        candidates.append((float(ohlcv_price), ohlcv_ts, "ohlcv"))

    if candidates:
        # Para SL, considera o MENOR preço entre as fontes (mais agressivo
        # no negativo). Para TP, o MAIOR (mais agressivo no positivo).
        # Precedência SL ANTES de TP — mesma ordem do scan 1m legado
        # (`_check_outcome_for_candle`) e do `TradeMonitorService` em
        # produção: política conservadora, perda reconhecida primeiro
        # quando ambos os lados são tocados no mesmo tick.
        max_price, max_ts, max_src = max(candidates, key=lambda c: c[0])
        min_price, min_ts, min_src = min(candidates, key=lambda c: c[0])

        live_outcome: Optional[str] = None
        chosen_price: Optional[float] = None
        chosen_ts: Optional[datetime] = None
        chosen_src: Optional[str] = None
        if min_price <= sl:
            live_outcome = "SL_HIT"
            chosen_price, chosen_ts, chosen_src = min_price, min_ts, min_src
        elif max_price >= tp:
            live_outcome = "TP_HIT"
            chosen_price, chosen_ts, chosen_src = max_price, max_ts, max_src

        if live_outcome is not None:
            outcome = live_outcome
            exit_price = sl if outcome == "SL_HIT" else tp
            # exit_ts = max(chosen_ts, entry_ts) — holding_seconds não
            # negativo. Se `chosen_ts` for NULL, usa entry_timestamp.
            if chosen_ts is None:
                exit_ts = shadow.entry_timestamp or datetime.now(timezone.utc)
            elif shadow.entry_timestamp and chosen_ts < shadow.entry_timestamp:
                exit_ts = shadow.entry_timestamp
            else:
                exit_ts = chosen_ts
            logger.info(
                "[shadow-monitor] live-close shadow_id=%s symbol=%s "
                "outcome=%s src=%s entry=%.8f chosen=%.8f tp=%.8f sl=%.8f "
                "mm=(%s,%s) ohlcv=(%s,%s)",
                shadow.id, shadow.symbol, outcome, chosen_src,
                entry_price, chosen_price, tp, sl,
                mm_price, mm_ts, ohlcv_price, ohlcv_ts,
            )
            _finalize_outcome(shadow, outcome, exit_price, exit_ts, entry_price)
            # FIX D1 (2026-05-15): _capture_exit_features + record_as_simulation
            # movidos para _record_simulation_one_async (sessão isolada, pós-
            # commit). Qualquer SQL error lá não aborta esta tx principal.
            return "completed"

    after_ts = shadow.last_processed_time or shadow.entry_timestamp
    candles = await _fetch_candles(
        db, shadow.symbol, after_ts, SHADOW_MONITOR_MAX_CANDLES_PER_RUN
    )
    if not candles:
        # ── FIX B2 (additive, 2026-05-15) — Timeout por elapsed time ───
        # Muitos símbolos do pool não têm OHLCV 1m ingerido; quando o
        # `_fetch_candles` devolve [], o branch TIMEOUT do scan candle-a-
        # candle (abaixo) NUNCA é alcançado e trades ficam RUNNING
        # indefinidamente mesmo após >24h. Aqui fechamos por TIMEOUT
        # quando o tempo decorrido (em minutos) já ultrapassa
        # `timeout_candles` (definido em minutos para candles 1m).
        #
        # Posição CRÍTICA: este early-check vive DENTRO do `if not
        # candles` propositalmente. Se houver candles 1m disponíveis,
        # o loop abaixo prevalece (TP/SL detectados intra-candle ainda
        # têm prioridade sobre TIMEOUT, por candle, mesma regra
        # histórica). Mexer para fora deste `if` quebra a regra
        # canônica TP/SL > TIMEOUT em cenários de retrace (preço tocou
        # TP/SL em candle pendente mas voltou; sem este guard, o
        # TIMEOUT marcaria o trade antes de o scan ver o hit real).
        #
        # Nunca propaga falha (mesma política de `_capture_exit_features`
        # / `record_as_simulation`): se algo aqui levanta, fallback é
        # manter o status atual e tentar de novo no próximo tick.
        timeout_candles_m = int(shadow.timeout_candles or 0) or None
        if timeout_candles_m and shadow.entry_timestamp is not None:
            now_utc = datetime.now(timezone.utc)
            elapsed_minutes = (
                now_utc - shadow.entry_timestamp
            ).total_seconds() / 60.0
            if elapsed_minutes >= timeout_candles_m:
                # exit_price: melhor preço corrente disponível
                # (mm > ohlcv > entry_price). Coerente com a precedência
                # do live-close.
                if mm_price is not None:
                    exit_price_to = float(mm_price)
                elif ohlcv_price is not None:
                    exit_price_to = float(ohlcv_price)
                else:
                    exit_price_to = entry_price
                logger.info(
                    "[shadow-monitor] timeout-elapsed shadow_id=%s "
                    "symbol=%s elapsed_min=%.1f timeout_candles=%d "
                    "exit_price=%.8f entry=%.8f (no 1m candles to scan)",
                    shadow.id, shadow.symbol, elapsed_minutes,
                    timeout_candles_m, exit_price_to, entry_price,
                )
                _finalize_outcome(
                    shadow, "TIMEOUT", exit_price_to, now_utc, entry_price
                )
                # FIX D1 (2026-05-15): _capture_exit_features + record_as_simulation
                # movidos para _record_simulation_one_async (sessão isolada, pós-
                # commit). Qualquer SQL error lá não aborta esta tx principal.
                return "completed"
        # Sem candles novas — mantém status atual.
        return "pending" if shadow.status == "PENDING" else "running"

    # Quantas candles já vimos antes deste tick?
    timeout_candles = int(shadow.timeout_candles or 0) or None
    candles_seen_before = 0
    if timeout_candles and shadow.last_processed_time and shadow.entry_timestamp:
        delta = shadow.last_processed_time - shadow.entry_timestamp
        candles_seen_before = max(int(delta.total_seconds() // 60), 0)

    outcome: Optional[str] = None
    exit_price: Optional[float] = None
    exit_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None

    for idx, c in enumerate(candles, start=1):
        last_seen_ts = c["time"]
        if c["high"] is None or c["low"] is None:
            continue
        # SL antes de TP na mesma candle — regra conservadora.
        if c["low"] <= sl:
            outcome = "SL_HIT"
            exit_price = sl
            exit_ts = c["time"]
            break
        if c["high"] >= tp:
            outcome = "TP_HIT"
            exit_price = tp
            exit_ts = c["time"]
            break
        # Timeout (count global desde entry, NÃO só dentro do tick atual).
        if timeout_candles and (candles_seen_before + idx) >= timeout_candles:
            outcome = "TIMEOUT"
            exit_price = c["close"] if c["close"] is not None else c["open"]
            exit_ts = c["time"]
            break

    if outcome is None:
        # Não atingiu TP/SL/timeout dentro da janela — registra progresso
        # e passa pra próxima execução.
        shadow.last_processed_time = last_seen_ts
        if shadow.status == "PENDING":
            shadow.status = "RUNNING"
            return "running"
        return "running"

    # Outcome atingido (caminho candle-a-candle 1m) — usa os mesmos
    # helpers do live-close.
    _finalize_outcome(shadow, outcome, exit_price, exit_ts, entry_price)
    # FIX D1 (2026-05-15): _capture_exit_features + record_as_simulation
    # movidos para _record_simulation_one_async (sessão isolada, pós-
    # commit). Qualquer SQL error lá não aborta esta tx principal.
    return "completed"


async def _monitor_async() -> Dict[str, int]:
    """Uma execução do monitor — processa até ``BATCH_SIZE`` shadows."""
    from ..database import CeleryAsyncSessionLocal

    summary = {"processed": 0, "completed": 0, "errors": 0, "backfill_created": 0}
    # Coletados dentro da tx principal e consumidos após o commit — FIX C3/D1.
    enrich_targets: List[Dict[str, Any]] = []
    # FIX D1 (2026-05-15): IDs dos shadows fechados neste tick — gravação de
    # simulação em sessão isolada APÓS o commit da tx principal.
    sim_targets: List[Any] = []

    async with CeleryAsyncSessionLocal() as db:
        async with db.begin():
            # Carrega batch determinístico (sorted by id) — gotcha #251/#273.
            # FOR UPDATE SKIP LOCKED garante que duas execuções
            # concorrentes do monitor (ad-hoc dispatch + beat tick
            # sobreposto, ou múltiplos workers da execution queue) NÃO
            # processem o mesmo shadow_trade no mesmo tick.
            res = await db.execute(
                select(ShadowTrade)
                .where(ShadowTrade.status.in_(("PENDING", "RUNNING")))
                .order_by(ShadowTrade.id.asc())
                .with_for_update(skip_locked=True)
                .limit(SHADOW_MONITOR_BATCH_SIZE)
            )
            shadows = list(res.scalars().all())
            # Re-sort defensivamente — ORM já devolve em ordem por
            # ORDER BY, mas sorted() reforça invariante deadlock-safety.
            shadows.sort(key=lambda s: s.id)

            for shadow in shadows:
                summary["processed"] += 1
                try:
                    transition = await _advance_shadow(db, shadow)
                    if transition == "completed":
                        summary["completed"] += 1
                        # FIX D1: coleta ID antes do commit para gravação
                        # de simulação em sessão isolada pós-commit.
                        sim_targets.append(shadow.id)
                except Exception:
                    summary["errors"] += 1
                    logger.exception(
                        "[shadow-monitor] advance failed for shadow_id=%s",
                        shadow.id,
                    )

            # Snapshot de dados escalares ANTES do commit — ORM objects
            # ficam detached após o fechamento da sessão. Filtro por
            # entry_timestamp não-NULL replica o guard de _enrich_market_context
            # (só enriquece shadows com entrada confirmada).
            enrich_targets = [
                {
                    "shadow_id": s.id,
                    "symbol": s.symbol,
                    "entry_timestamp": s.entry_timestamp,
                    "decision_id": s.decision_id,
                    "needs_fill": (
                        s.btc_price_at_entry is None
                        or s.btc_change_1h_pct is None
                        or s.funding_rate_at_entry is None
                        or s.n_concurrent_signals is None
                    ),
                }
                for s in shadows
                if s.entry_timestamp is not None
            ]
    # ── Pós-commit: operações best-effort em sessões isoladas ─────────────────
    # A tx principal já commitou todos os shadow.status='COMPLETED'. A partir
    # daqui, qualquer falha SQL não desfaz os fechamentos (FIX C3/D1).

    # Simulação ML best-effort — tx isolada por shadow (FIX D1, 2026-05-15).
    # record_as_simulation + _capture_exit_features usam db_sim próprio;
    # UndefinedColumnError ou lock em trade_simulations não aborta fechamentos.
    for shadow_id in sim_targets:
        await _record_simulation_one_async(shadow_id)

    # Enriquecimento ML best-effort — tx isolada por shadow (FIX C3, 2026-05-15).
    for t in enrich_targets:
        if t["needs_fill"]:
            await _enrich_one_async(
                t["shadow_id"],
                t["symbol"],
                t["entry_timestamp"],
                t["decision_id"],
            )

    # Backfill best-effort: cria shadows para símbolos aprovados na watchlist
    # sem shadow RUNNING. Cobre o gap entre pipeline_scan (apenas transições
    # de estado → decisions_log) e execute_buy (janela 10 min). Idempotente
    # via ON CONFLICT (decision_id) DO NOTHING.
    summary["backfill_created"] = await _backfill_shadows_for_all_users()

    return summary


async def _backfill_shadows_for_all_users() -> int:
    """Carrega SpotEngineConfig por usuário e chama safe_backfill_watchlist_shadows."""
    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig
    from ..services.shadow_trade_service import safe_backfill_watchlist_shadows
    from sqlalchemy import select

    try:
        async with CeleryAsyncSessionLocal() as db:
            cfg_res = await db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                )
            )
            cfg_rows = cfg_res.scalars().all()

        total = 0
        for cfg_row in cfg_rows:
            try:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                user_config = {
                    "tp_pct": float(se_cfg.selling.take_profit_pct),
                    "sl_pct": float(
                        se_cfg.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
                total += await safe_backfill_watchlist_shadows(cfg_row.user_id, user_config)
            except Exception:
                logger.exception(
                    "[shadow-monitor] backfill failed for user=%s", cfg_row.user_id
                )
        return total
    except Exception:
        logger.exception("[shadow-monitor] _backfill_shadows_for_all_users failed")
        return 0


@celery_app.task(name="app.tasks.shadow_trade_monitor.run", bind=True)
def run(self) -> str:
    """Beat-driven monitor — default a cada ``SHADOW_MONITOR_INTERVAL_S`` s."""
    try:
        result = _run_async(_monitor_async())
        backfill = result.get("backfill_created", 0)
        msg = (
            f"Shadow monitor: {result['processed']} processed, "
            f"{result['completed']} completed, {result['errors']} errors, "
            f"{backfill} backfill created"
        )
        logger.info("[shadow-monitor] %s", msg)
        return msg
    except Exception as exc:
        logger.error("[shadow-monitor] task failed: %s", exc, exc_info=True)
        raise
