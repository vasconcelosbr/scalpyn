"""Shadow Portfolio service — record promoções L3 que não viraram trade real.

Toda chamada pública deste módulo usa **own-session** (assim como
``decision_audit_service.safe_record_decision``): abre uma
``AsyncSessionLocal`` própria, faz INSERT/SELECT, commita e sai. Isso
garante que:

* nunca poisona a transação do caller (gotcha nested-savepoint
  documentada em ``replit.md``);
* nunca bloqueia o fluxo de execução real — todos os helpers ``safe_*``
  retornam silenciosamente em caso de erro (apenas loga);
* o pool budget aceita esses INSERTs (audit volume — ver
  ``docs/db-pool-budget.md``).

Princípios:

* ZERO HARDCODE: todo valor de negócio vem de env ou do ``user_config``
  passado pelo caller (que vem do ``SpotEngineConfig`` do usuário).
* IDEMPOTENTE: dois calls com a mesma ``decision_id`` resultam em uma
  única row em ``shadow_trades``.
* ADDITIVE: nada existente é alterado por este módulo.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.backoffice import DecisionLog
from ..models.shadow_trade import ShadowTrade

logger = logging.getLogger(__name__)


# Defaults vindos de env — ZERO HARDCODE de valor de negócio.
SHADOW_TRADE_AMOUNT_USDT = float(os.environ.get("SHADOW_TRADE_AMOUNT_USDT", "1000.0"))
SHADOW_TIMEOUT_CANDLES = int(os.environ.get("SHADOW_TIMEOUT_CANDLES", "1440"))  # 24h de 1m
SHADOW_LOOKBACK_MINUTES = int(os.environ.get("SHADOW_LOOKBACK_MINUTES", "10"))


# ── helpers internos ─────────────────────────────────────────────────────────

async def _next_1m_open(
    db: AsyncSession, symbol: str, after_ts: datetime
) -> tuple[Optional[float], Optional[datetime]]:
    """Open + timestamp da próxima candle 1m de ``ohlcv`` após ``after_ts``.

    Mantido como **fallback** legado para shadows criados antes do fluxo
    "entry-at-decision-time" (Task 2026-05-13). Novo fluxo prefere
    :func:`_get_current_price_multi_tf` para nunca deixar um shadow em
    PENDING — a entrada é o preço corrente no instante da decisão.
    """
    res = await db.execute(
        text(
            """
            SELECT open, time
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time > :t
             ORDER BY time ASC
             LIMIT 1
            """
        ),
        {"s": symbol, "t": after_ts},
    )
    row = res.fetchone()
    if row is None or row.open is None:
        return None, None
    return float(row.open), row.time


async def _get_current_price_multi_tf(
    db: AsyncSession, symbol: str
) -> tuple[Optional[float], Optional[datetime]]:
    """Último ``close`` disponível de ``ohlcv`` em qualquer timeframe rápido.

    Por que multi-timeframe: em produção o coletor não ingere 1m
    universalmente — muitos símbolos só têm 5m / 15m / 30m. Limitar a
    1m deixava o shadow eternamente sem entry_price (status PENDING),
    quebrando a expectativa do usuário de "executar trade simulado com
    o preço do momento" assim que a decisão ALLOW chega.

    Estratégia: pegar a candle MAIS RECENTE entre 1m/5m/15m/30m e usar
    ``close`` como preço de entrada simulado + ``time`` como
    ``entry_timestamp``. Cobertura prática perto de 100% do pool ativo.
    """
    res = await db.execute(
        text(
            """
            SELECT close, time
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe IN ('1m', '5m', '15m', '30m')
             ORDER BY time DESC
             LIMIT 1
            """
        ),
        {"s": symbol},
    )
    row = res.fetchone()
    if row is None or row.close is None:
        return None, None
    return float(row.close), row.time


async def _get_market_metadata_price(
    db: AsyncSession, symbol: str
) -> tuple[Optional[float], Optional[datetime]]:
    """Preço corrente vindo de ``market_metadata`` (ticker REST, refresh ~60s).

    Por que não ``ohlcv`` aqui (Task #292): muitos símbolos do pool só têm
    candle 5m/15m/30m. ``_get_current_price_multi_tf`` lê o close da candle
    fechada mais recente — pode ter até 30 min de defasagem em relação ao
    preço real do mercado. ``market_metadata.price`` é atualizado a cada
    ciclo de ``collect_all`` (≈60s) com o ticker REST do Gate.io e é
    exatamente a fonte que o frontend mostra como "current_price" para o
    usuário. Usar a mesma fonte garante que o monitor feche os trades
    consistentemente com o que o usuário vê na UI (TP/SL visíveis ⇒ TP/SL
    fechados).
    """
    res = await db.execute(
        text(
            """
            SELECT price, last_updated
              FROM market_metadata
             WHERE symbol = :s
            """
        ),
        {"s": symbol},
    )
    row = res.fetchone()
    if row is None or row.price is None:
        return None, None
    try:
        price = float(row.price)
    except (TypeError, ValueError):
        return None, None
    return price, row.last_updated


async def _resolve_decision(
    db: AsyncSession, user_id, symbol: str, lookback_minutes: int
) -> Optional[DecisionLog]:
    """Mais recente promoção L3 spot (decision='ALLOW' AND direction='SPOT')
    para (user_id, symbol) dentro da janela de lookback.

    Vocabulário canônico (Task #292): ``decisions_log.direction`` usa
    ``'LONG' | 'SHORT' | 'NEUTRAL' | 'SPOT'`` (uppercase). Shadow é
    **spot-only** hoje (capital simulado U$1000 USDT, sem leverage,
    long-only) — por isso filtramos APENAS por ``'SPOT'``, NÃO por
    ``'LONG'``. Aceitar ``'LONG'`` aqui contaminaria shadow_trades com
    decisões futures cuja semântica de TP/SL/timeout é diferente.

    Quando o Shadow para futures for habilitado (follow-up), criar um
    helper separado ``_resolve_futures_decision`` ou expandir o filtro
    com guard explícito de ``market_mode``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    q = (
        select(DecisionLog)
        .where(
            DecisionLog.user_id == user_id,
            DecisionLog.symbol == symbol,
            DecisionLog.decision == "ALLOW",
            DecisionLog.direction == "SPOT",
            DecisionLog.created_at >= cutoff,
        )
        .order_by(DecisionLog.created_at.desc())
        .limit(1)
    )
    res = await db.execute(q)
    return res.scalar_one_or_none()


def _build_features_snapshot(decision: DecisionLog) -> Dict[str, Any]:
    """Constrói features_snapshot FLAT a partir de ``decision.metrics``.

    Crítico para Fase 6 (ML): ``DatasetBuilder.extract_features`` lê
    ``features_snapshot.get(feat)`` esperando escalares (float/bool/None)
    e chama ``float(value)`` se não for None. Mas
    ``decisions_log.metrics["indicators_snapshot"]`` (escrito por
    ``indicators_provider.build_indicators_snapshot``) tem forma
    ``{key: {"value": …, "source_group": …, "ts": …, "stale": …}}``.
    Copiar essa estrutura crua quebra o DatasetBuilder
    (``float({"value": …})`` → TypeError) e contamina o dataset com
    NaNs/zeros mascarados.

    Aqui achatamos para ``{key: scalar}`` extraindo apenas ``value``.
    Forward-compatible: qualquer key extra (além das ALL_BASE_FEATURES
    do builder) é preservada — ``.get()`` simplesmente ignora as
    desconhecidas. Keys ausentes (ex.: ``ema9``, ``volume_24h_usdt``)
    viram None no extract e são preenchidas com 0.0 pelo
    ``prepare_dataset.fillna``.
    """
    metrics = decision.metrics or {}
    snap = metrics.get("indicators_snapshot") or {}
    if not isinstance(snap, dict):
        return {}
    flat: Dict[str, Any] = {}
    for key, entry in snap.items():
        if isinstance(entry, dict) and "value" in entry:
            flat[key] = entry.get("value")
        else:
            # Defensive: se um caller futuro persistir flat direto, mantém.
            flat[key] = entry
    return flat


# ── market context enrichment (migration 052, ML Fase 6) ────────────────────


# Símbolo canônico do BTC no banco (mesmo formato dos demais ativos —
# subscrito por `_USDT`). Usado pelas duas leituras de contexto BTC.
BTC_CONTEXT_SYMBOL = os.environ.get("SHADOW_BTC_CONTEXT_SYMBOL", "BTC_USDT")


async def enrich_market_context(
    db: AsyncSession,
    *,
    symbol: str,
    entry_timestamp: datetime,
    decision_id: Optional[int] = None,
) -> Dict[str, Optional[float]]:
    """Calcula os 4 campos de contexto de mercado para um shadow trade.

    Retorna sempre um dict com as 4 chaves (``btc_price_at_entry``,
    ``btc_change_1h_pct``, ``funding_rate_at_entry``,
    ``n_concurrent_signals``); valores faltantes vêm como ``None``
    (o caller decide se sobrescreve a coluna ou mantém o que já tinha).

    NUNCA falha: qualquer exceção isolada de cada query é convertida em
    ``None`` para a chave correspondente. Esse contrato garante que
    o monitor pode chamar essa função no caminho quente sem afetar
    TP/SL/timeout em caso de schema parcial / lock momentâneo.

    Fontes
    ------
    * ``btc_price_at_entry`` / ``btc_change_1h_pct``: ``ohlcv`` para
      ``BTC_USDT`` no timeframe ``1h``. Pega a candle mais recente
      ``time <= entry_timestamp`` (close = preço BTC âncora) e a candle
      anterior dentro de uma janela de 75 min para cobrir gaps de
      ingestão.
    * ``funding_rate_at_entry``: ``funding_rates`` (schema:
      ``time TIMESTAMPTZ, symbol VARCHAR(20), exchange VARCHAR(50),
      rate NUMERIC(10,6)``) — última leitura ``time <= entry_timestamp``
      do mesmo símbolo. Spot puro pode não ter funding (NULL — não
      falha).
    * ``n_concurrent_signals``: contagem de ``DISTINCT symbol`` em
      ``decisions_log`` com ``decision='ALLOW'`` no MESMO minuto da
      entrada. Inclui o próprio sinal (proxy de "concorrência" que o
      ML pode normalizar dividindo por 1).
    """
    out: Dict[str, Optional[float]] = {
        "btc_price_at_entry": None,
        "btc_change_1h_pct": None,
        "funding_rate_at_entry": None,
        "n_concurrent_signals": None,
    }
    if entry_timestamp is None:
        return out

    # ── BTC anchor + 1h change ────────────────────────────────────────────
    try:
        res = await db.execute(
            text(
                """
                SELECT close, time
                  FROM ohlcv
                 WHERE symbol = :s
                   AND timeframe = '1h'
                   AND time <= :t
                 ORDER BY time DESC
                 LIMIT 1
                """
            ),
            {"s": BTC_CONTEXT_SYMBOL, "t": entry_timestamp},
        )
        cur_row = res.fetchone()
        if cur_row is not None and cur_row.close is not None:
            cur_close = float(cur_row.close)
            out["btc_price_at_entry"] = cur_close

            prev_res = await db.execute(
                text(
                    """
                    SELECT close
                      FROM ohlcv
                     WHERE symbol = :s
                       AND timeframe = '1h'
                       AND time <= :t_anchor - interval '60 minutes'
                       AND time >= :t_anchor - interval '135 minutes'
                     ORDER BY time DESC
                     LIMIT 1
                    """
                ),
                {"s": BTC_CONTEXT_SYMBOL, "t_anchor": cur_row.time},
            )
            prev_row = prev_res.fetchone()
            if prev_row is not None and prev_row.close is not None:
                prev_close = float(prev_row.close)
                if prev_close > 0:
                    out["btc_change_1h_pct"] = (
                        (cur_close - prev_close) / prev_close * 100.0
                    )
    except Exception:
        logger.exception(
            "[shadow] enrich_market_context: BTC OHLCV lookup failed "
            "(symbol=%s entry_ts=%s)",
            symbol, entry_timestamp,
        )

    # ── funding rate ──────────────────────────────────────────────────────
    try:
        res = await db.execute(
            text(
                """
                SELECT rate
                  FROM funding_rates
                 WHERE symbol = :s
                   AND time <= :t
                 ORDER BY time DESC
                 LIMIT 1
                """
            ),
            {"s": symbol, "t": entry_timestamp},
        )
        row = res.fetchone()
        if row is not None and row.rate is not None:
            out["funding_rate_at_entry"] = float(row.rate)
    except Exception:
        logger.exception(
            "[shadow] enrich_market_context: funding_rates lookup failed "
            "(symbol=%s entry_ts=%s)",
            symbol, entry_timestamp,
        )

    # ── concurrent signals (mesmo minuto, ALLOW) ─────────────────────────
    try:
        res = await db.execute(
            text(
                """
                SELECT COUNT(DISTINCT symbol) AS n
                  FROM decisions_log
                 WHERE decision = 'ALLOW'
                   AND date_trunc('minute', created_at)
                     = date_trunc('minute', CAST(:t AS timestamptz))
                """
            ),
            {"t": entry_timestamp},
        )
        row = res.fetchone()
        if row is not None and row.n is not None:
            out["n_concurrent_signals"] = int(row.n)
    except Exception:
        logger.exception(
            "[shadow] enrich_market_context: concurrent_signals lookup failed "
            "(symbol=%s entry_ts=%s decision_id=%s)",
            symbol, entry_timestamp, decision_id,
        )

    return out


# ── core: criação a partir de uma DecisionLog ────────────────────────────────

_INSERT_SHADOW_SQL = text("""
    INSERT INTO shadow_trades (
        decision_id, user_id, symbol, strategy, direction,
        amount_usdt, entry_price, entry_timestamp,
        tp_price, sl_price, tp_pct, sl_pct, timeout_candles,
        status, skip_reason, config_snapshot, features_snapshot,
        last_processed_time
    ) VALUES (
        :decision_id, :user_id, :symbol, :strategy, :direction,
        :amount_usdt, :entry_price, :entry_timestamp,
        :tp_price, :sl_price, :tp_pct, :sl_pct, :timeout_candles,
        :status, :skip_reason,
        CAST(:config_snapshot AS JSONB),
        CAST(:features_snapshot AS JSONB),
        :last_processed_time
    )
    ON CONFLICT (decision_id) DO NOTHING
    RETURNING id
""")


async def _create_from_decision(
    db: AsyncSession,
    decision: DecisionLog,
    skip_reason: str,
    user_config: Dict[str, Any],
) -> Optional[UUID]:
    """Insere uma row em ``shadow_trades`` para a ``decision`` informada.

    Idempotente em nível de banco via ``ON CONFLICT (decision_id) DO
    NOTHING`` (UNIQUE INDEX criado na migration 047). Retorna o ``id``
    da row criada, ou ``None`` se já existia (race entre workers
    concorrentes ou re-call).

    Caller deve estar dentro de uma sessão own-session (usar
    ``safe_create_*`` abaixo).
    """
    import json

    tp_pct = float(user_config.get("tp_pct") or 0.0)
    sl_pct = float(user_config.get("sl_pct") or 0.0)
    timeout_candles = int(user_config.get("timeout_candles") or SHADOW_TIMEOUT_CANDLES)

    # Entry-at-decision-time (Task 2026-05-13): preço corrente no
    # instante da decisão ALLOW vira entry_price imediatamente.
    # Fallback para next-1m-open mantém compat caso o pool não tenha
    # NENHUMA candle recente do símbolo (cenário raro, e ainda assim o
    # monitor reprocessa via _ensure_entry).
    entry_price, entry_ts = await _get_current_price_multi_tf(
        db, decision.symbol
    )
    initial_status = "RUNNING"
    if entry_price is None:
        entry_price, entry_ts = await _next_1m_open(
            db, decision.symbol, decision.created_at
        )
        if entry_price is None:
            initial_status = "PENDING"

    if entry_price is not None and entry_price > 0 and tp_pct > 0 and sl_pct > 0:
        tp_price = entry_price * (1 + tp_pct / 100.0)
        sl_price = entry_price * (1 - sl_pct / 100.0)
    else:
        tp_price = None
        sl_price = None

    config_snap = {
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "timeout_candles": timeout_candles,
        "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
    }
    features_snap = _build_features_snapshot(decision)

    res = await db.execute(
        _INSERT_SHADOW_SQL,
        {
            "decision_id": decision.id,
            "user_id": decision.user_id,
            "symbol": decision.symbol,
            "strategy": decision.strategy,
            # Vocabulário canônico (Task #292): UPPERCASE
            # 'SPOT' | 'LONG' | 'SHORT' | 'NEUTRAL'. Herda de
            # ``decisions_log.direction`` quando presente; fallback
            # para 'SPOT' (shadow é spot-only por enquanto, e os 515
            # registros antigos com direction=NULL no decisions_log
            # eram todos spot — ver migration 049).
            "direction": (getattr(decision, "direction", None) or "SPOT"),
            "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
            "entry_price": entry_price,
            "entry_timestamp": entry_ts,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "tp_pct": tp_pct or None,
            "sl_pct": sl_pct or None,
            "timeout_candles": timeout_candles,
            "status": initial_status,
            "skip_reason": skip_reason,
            "config_snapshot": json.dumps(config_snap, default=str),
            "features_snapshot": json.dumps(features_snap, default=str),
            # Bookmark do monitor: começa em entry_ts para que a
            # primeira janela de candles avaliada seja "tudo após a
            # entrada" (e não tudo desde sempre).
            "last_processed_time": entry_ts,
        },
    )
    row = res.fetchone()
    return row[0] if row is not None else None


# ── public own-session helpers ───────────────────────────────────────────────

async def safe_create_from_symbol_skip(
    user_id,
    symbol: str,
    skip_reason: str,
    user_config: Dict[str, Any],
    lookback_minutes: int = SHADOW_LOOKBACK_MINUTES,
) -> None:
    """Cria 1 shadow trade para a promoção L3 mais recente desse símbolo.

    Fire-and-forget: nunca raise. Se não existe promoção recente
    matching, loga WARNING e retorna.

    Use em gates per-symbol (cooldown, per-asset capital, NOT_TRADABLE).
    """
    from ..database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as own_db:
            async with own_db.begin():
                decision = await _resolve_decision(
                    own_db, user_id, symbol, lookback_minutes
                )
                if decision is None:
                    logger.warning(
                        "[shadow] no recent L3 ALLOW/up for user=%s symbol=%s "
                        "(lookback=%dmin) — skip_reason=%s not recorded",
                        user_id, symbol, lookback_minutes, skip_reason,
                    )
                    return
                created_id = await _create_from_decision(
                    own_db, decision, skip_reason, user_config
                )
                if created_id is not None:
                    logger.info(
                        "[shadow] created id=%s symbol=%s decision_id=%s "
                        "skip_reason=%s",
                        created_id, decision.symbol, decision.id, skip_reason,
                    )
    except Exception:
        logger.exception(
            "[shadow] safe_create_from_symbol_skip failed "
            "(user=%s symbol=%s skip_reason=%s)",
            user_id, symbol, skip_reason,
        )


async def safe_bulk_create_from_user_skip(
    user_id,
    skip_reason: str,
    user_config: Dict[str, Any],
    lookback_minutes: int = SHADOW_LOOKBACK_MINUTES,
) -> int:
    """Cria 1 shadow trade por promoção L3 recente do usuário sem shadow.

    Fire-and-forget: nunca raise. Retorna o número de shadow_trades
    criados (zero se nenhum elegível).

    Use em gates user-level (capital insuficiente, max_positions etc.)
    onde TODAS as promoções L3 daquele ciclo são barradas. Itera
    promoções recentes que ainda não têm shadow_trade.
    """
    from ..database import AsyncSessionLocal

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    created_count = 0
    try:
        async with AsyncSessionLocal() as own_db:
            async with own_db.begin():
                # Promoções recentes do usuário ainda sem shadow_trade
                # (anti-join via NOT EXISTS para idempotência)
                rows = await own_db.execute(
                    text(
                        """
                        SELECT d.id
                          FROM decisions_log d
                         WHERE d.user_id = :uid
                           AND d.decision = 'ALLOW'
                           AND d.direction = 'SPOT'
                           AND d.created_at >= :cutoff
                           AND NOT EXISTS (
                               SELECT 1 FROM shadow_trades s
                                WHERE s.decision_id = d.id
                           )
                         ORDER BY d.created_at DESC
                        """
                    ),
                    {"uid": user_id, "cutoff": cutoff},
                )
                ids = [r.id for r in rows.fetchall()]
                # Determinismo: ordena IDs antes de iterar para evitar
                # deadlock 40P01 entre workers concorrentes (gotcha #251/#273).
                for did in sorted(ids):
                    res = await own_db.execute(
                        select(DecisionLog).where(DecisionLog.id == did).limit(1)
                    )
                    decision = res.scalar_one_or_none()
                    if decision is None:
                        continue
                    try:
                        new_id = await _create_from_decision(
                            own_db, decision, skip_reason, user_config
                        )
                        if new_id is not None:
                            created_count += 1
                    except Exception:
                        logger.exception(
                            "[shadow] bulk create failed for decision_id=%s",
                            decision.id,
                        )
        if created_count:
            logger.info(
                "[shadow] bulk created %d shadow_trades for user=%s skip_reason=%s",
                created_count, user_id, skip_reason,
            )
    except Exception:
        logger.exception(
            "[shadow] safe_bulk_create_from_user_skip failed "
            "(user=%s skip_reason=%s)",
            user_id, skip_reason,
        )
    return created_count


_INSERT_SIM_SQL = text("""
    INSERT INTO trade_simulations (
        id, symbol, timestamp_entry, entry_price,
        tp_price, sl_price,
        exit_price, exit_timestamp,
        result, time_to_result,
        direction, is_simulated, source,
        decision_type, decision_id,
        features_snapshot, config_snapshot,
        created_at
    ) VALUES (
        gen_random_uuid(), :symbol, :timestamp_entry, :entry_price,
        :tp_price, :sl_price,
        :exit_price, :exit_timestamp,
        :result, :time_to_result,
        :direction, TRUE, :source,
        :decision_type, :decision_id,
        CAST(:features_snapshot AS JSONB),
        CAST(:config_snapshot AS JSONB),
        NOW()
    )
    ON CONFLICT (decision_id) WHERE source = 'SHADOW'
        AND decision_id IS NOT NULL DO NOTHING
    RETURNING id
""")


async def record_as_simulation(
    db: AsyncSession,
    shadow: ShadowTrade,
) -> Optional[UUID]:
    """Replica um ``ShadowTrade`` COMPLETED em ``trade_simulations``.

    Idempotente via WHERE NOT EXISTS (decision_id, source='SHADOW') —
    como ``trade_simulations`` não tem UNIQUE em ``decision_id`` (FK
    nullable + ondelete=SET NULL), usamos anti-join atômico no INSERT
    pra evitar race entre execuções concorrentes do monitor.

    Retorna o ``id`` da simulação criada, ou ``None`` se já existia.

    Mapeamento:
      * outcome 'TP_HIT' → result='WIN'
      * outcome 'SL_HIT' → result='LOSS'
      * outcome 'TIMEOUT' → result='TIMEOUT'
      * direction sempre 'SPOT' (shadow é spot-only)
      * decision_type sempre 'ALLOW' (shadow só nasce de promoção L3)
      * source = 'SHADOW'
    """
    outcome = (shadow.outcome or "").upper()
    if outcome == "TP_HIT":
        result = "WIN"
    elif outcome == "SL_HIT":
        result = "LOSS"
    elif outcome == "TIMEOUT":
        result = "TIMEOUT"
    else:
        logger.warning(
            "[shadow] record_as_simulation skipped — unknown outcome=%r for shadow_id=%s",
            shadow.outcome, shadow.id,
        )
        return None

    if (
        shadow.entry_price is None
        or shadow.tp_price is None
        or shadow.sl_price is None
        or shadow.exit_price is None
        or shadow.entry_timestamp is None
        or shadow.exit_timestamp is None
    ):
        logger.warning(
            "[shadow] record_as_simulation skipped — missing price/timestamp "
            "for shadow_id=%s (entry=%s tp=%s sl=%s exit=%s)",
            shadow.id, shadow.entry_price, shadow.tp_price,
            shadow.sl_price, shadow.exit_price,
        )
        return None

    import json

    res = await db.execute(
        _INSERT_SIM_SQL,
        {
            "symbol": shadow.symbol,
            "timestamp_entry": shadow.entry_timestamp,
            "entry_price": shadow.entry_price,
            "tp_price": shadow.tp_price,
            "sl_price": shadow.sl_price,
            "exit_price": shadow.exit_price,
            "exit_timestamp": shadow.exit_timestamp,
            "result": result,
            "time_to_result": shadow.holding_seconds,
            "direction": "SPOT",
            "source": "SHADOW",
            "decision_type": "ALLOW",
            "decision_id": shadow.decision_id,
            "features_snapshot": json.dumps(shadow.features_snapshot or {}, default=str),
            "config_snapshot": json.dumps(shadow.config_snapshot or {}, default=str),
        },
    )
    row = res.fetchone()
    if row is None:
        logger.info(
            "[shadow] simulation already exists for decision_id=%s source=SHADOW "
            "(shadow_id=%s) — dedup hit",
            shadow.decision_id, shadow.id,
        )
        return None
    sim_id = row[0]
    logger.info(
        "[shadow] simulation recorded id=%s shadow_id=%s decision_id=%s "
        "result=%s symbol=%s",
        sim_id, shadow.id, shadow.decision_id, result, shadow.symbol,
    )
    return sim_id


async def get_pending(
    db: AsyncSession,
    user_id,
    limit: int = 100,
) -> List[ShadowTrade]:
    """Shadow trades em PENDING/RUNNING do usuário, ordenados por created_at ASC.

    Caller-session: este helper SE espera ser chamado dentro da
    sessão do caller (ex: monitor da Fase 3, endpoints da Fase 5).
    """
    q = (
        select(ShadowTrade)
        .where(
            ShadowTrade.user_id == user_id,
            ShadowTrade.status.in_(("PENDING", "RUNNING")),
        )
        .order_by(ShadowTrade.created_at.asc())
        .limit(limit)
    )
    res = await db.execute(q)
    return list(res.scalars().all())


# ── reason mapping ───────────────────────────────────────────────────────────

def map_capital_reason(reason_text: str) -> str:
    """Mapeia o texto de ``SpotCapitalManager.can_open_new_position`` para skip_reason."""
    t = (reason_text or "").lower()
    if "insufficient" in t:
        return "INSUFFICIENT_CAPITAL"
    if "max_positions_total" in t:
        return "MAX_POSITIONS"
    if "max_capital_in_use" in t:
        return "MAX_CAPITAL_IN_USE"
    return "CAPITAL_GATE"


def map_per_asset_reason(reason_text: str) -> str:
    """Mapeia o texto de ``SpotCapitalManager.can_trade_asset`` para skip_reason."""
    t = (reason_text or "").lower()
    if "max_positions_per_asset" in t:
        return "MAX_POSITIONS_PER_ASSET"
    if "max_exposure" in t:
        return "MAX_EXPOSURE_PER_ASSET"
    return "ASSET_GATE"
