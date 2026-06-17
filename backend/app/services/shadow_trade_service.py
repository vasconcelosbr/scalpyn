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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.backoffice import DecisionLog
from ..models.shadow_trade import ShadowTrade
from . import shadow_metrics

logger = logging.getLogger(__name__)


# ── Synthetic decision (Task #303) ──────────────────────────────────────────
#
# ``pipeline_scan._should_log_decision`` só grava em ``decisions_log`` em
# transições (BLOCK→ALLOW, mudança de direção, delta de score). Símbolos
# cronicamente aprovados em L3 com score estável NUNCA têm uma DecisionLog
# correspondente. Para o Shadow promover esses símbolos, construímos uma
# "decisão sintética" a partir do snapshot vivo de
# ``pipeline_watchlist_assets`` — mesmos atributos esperados por
# ``_create_from_decision`` (duck-typing), mas ``id=None`` para que o
# INSERT em ``shadow_trades`` grave ``decision_id=NULL`` (migration 057).

@dataclass
class _SyntheticDecision:
    """Decisão construída a partir do estado vivo da L3.

    Compatível por duck-typing com ``DecisionLog`` no único caminho que
    consome o objeto (``_create_from_decision``): acesso a ``.id``,
    ``.user_id``, ``.symbol``, ``.strategy``, ``.direction``, ``.metrics``
    e ``.created_at``. ``id=None`` é o sinal canônico de "sem decision_id
    real" — propagado como NULL na coluna ``shadow_trades.decision_id``.
    """
    user_id: Any
    symbol: str
    direction: str = "SPOT"
    strategy: Optional[str] = None
    id: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metrics: Dict[str, Any] = field(default_factory=dict)


# Defaults vindos de env — ZERO HARDCODE de valor de negócio.
SHADOW_TRADE_AMOUNT_USDT = float(os.environ.get("SHADOW_TRADE_AMOUNT_USDT", "1000.0"))
SHADOW_TIMEOUT_CANDLES = int(os.environ.get("SHADOW_TIMEOUT_CANDLES", "1440"))  # 24h de 1m
SHADOW_LOOKBACK_MINUTES = int(os.environ.get("SHADOW_LOOKBACK_MINUTES", "10"))
# ── Shadow-specific TP/SL overrides (Zero Hardcode) ──────────────────────────
# When set, these override the spot engine profile's take_profit_pct /
# max_drawdown_from_hwm_pct for shadow trade simulations. This decouples
# the simulation band from the production config — e.g. SHADOW_TP_PCT=1.0
# and SHADOW_SL_PCT=1.0 create a symmetric ±1% band for ML training while
# the real profile can use different TP/SL targets.
# 0.0 means "not overridden; fall back to spot engine config value".
_SHADOW_TP_PCT_OVERRIDE = float(os.environ.get("SHADOW_TP_PCT", "0.0"))
_SHADOW_SL_PCT_OVERRIDE = float(os.environ.get("SHADOW_SL_PCT", "0.0"))


def _apply_barrier_params(user_config: dict, ml_config: dict) -> dict:
    """Merge ATR barrier parameters from ml_config into user_config.

    When shadow_barrier_mode='ATR_DYNAMIC', _create_from_decision will
    override sl_pct (and optionally tp_pct) using per-asset atr_percent.
    FIXED mode (default) preserves existing tp_pct/sl_pct unchanged.
    """
    user_config["shadow_barrier_mode"] = ml_config.get("shadow_barrier_mode", "FIXED")
    user_config["sl_atr_multiplier"]   = ml_config.get("shadow_atr_multiplier_sl", 1.5)
    user_config["sl_min_pct"]          = ml_config.get("shadow_barrier_min_pct", 0.5)
    user_config["sl_max_pct"]          = ml_config.get("shadow_barrier_max_pct", 3.0)
    # shadow_tp_pct: when set, overrides tp_pct for all modes (P0-1 fix: 1.5)
    _tp_override = ml_config.get("shadow_tp_pct")
    if _tp_override:
        user_config["shadow_tp_pct"] = float(_tp_override)
    return user_config

# ── TTT Policy defaults (Zero Hardcode — override via Cloud Run env vars) ─────
# Valores de negócio definidos em config_profiles (config_type='ttt_policy').
# Env vars são usados como fallback quando DB não está disponível na criação.
TTT_ENABLED_DEFAULT = os.environ.get("TTT_ENABLED", "true").lower() == "true"
TTT_TP_PCT_DEFAULT = float(os.environ.get("TTT_TP_PCT", "1.0"))
TTT_TIMEOUT_MINUTES_DEFAULT = int(os.environ.get("TTT_TIMEOUT_MINUTES", "180"))

SHADOW_SOURCE_L3 = "L3"
# Shadows de ativos rejeitados na L3 — usados exclusivamente para dados ML.
# Segregados por source para nunca contaminar métricas de aprovados.
SHADOW_SOURCE_L3_REJECTED = "L3_REJECTED"
# BLOCO B — espectro completo da watchlist L1 spot (nova arquitetura ML).
# Captura TODOS os símbolos pós-filtro estrutural, não apenas aprovados L3.
# Isso elimina o viés de seleção que causava winrate ~76% no dataset antigo.
# WATCHLIST_FUT reservado para fase futures (não criar agora).
SHADOW_SOURCE_WATCHLIST_SPOT = "WATCHLIST_SPOT"
# L1_SPECTRUM — fonte exclusiva de treino do ML (migration 073+).
# Capturado na promoção L1, ANTES de qualquer filtro de qualidade.
# Stream B (L3) continua em paralelo para validação de política.
SHADOW_SOURCE_L1_SPECTRUM = "L1_SPECTRUM"
# Camada contrafactual: shadow para TODOS os ativos que chegaram ao gate L3,
# independente da decisão ALLOW/BLOCK. Usado para análise de política e ML.
SHADOW_SOURCE_L3_SIMULATED = "L3_SIMULATED"
_VALID_SHADOW_SOURCES = (
    SHADOW_SOURCE_L3,
    SHADOW_SOURCE_L3_REJECTED,
    SHADOW_SOURCE_WATCHLIST_SPOT,
    SHADOW_SOURCE_L1_SPECTRUM,
    SHADOW_SOURCE_L3_SIMULATED,
)


# ── helpers internos ─────────────────────────────────────────────────────────


def _validate_temporal_param(
    value: Any,
    *,
    param_name: str,
    symbol: Optional[str] = None,
    decision_id: Optional[Any] = None,
) -> Optional[datetime]:
    """Valida que ``value`` pode ser bound como timestamptz numa query raw.

    Defesa contra o storm Cloud SQL 2026-05-19/20 (Task #309): qualquer
    `timedelta`, `None`, `str`, `int`, etc. passado como parâmetro de
    `time <= :param` quebra com ``operator does not exist: timestamp
    with time zone <= interval`` (asyncpg encoda timedelta como
    INTERVAL). A transação aborta, e toda statement subsequente naquela
    sessão cai em cascata com ``current transaction is aborted``.

    Retorna o próprio ``datetime`` se válido; ``None`` (com log de erro
    estruturado) caso contrário. O caller DEVE abortar APENAS o bloco
    que depende desse parâmetro — não a função inteira — para preservar
    enriquecimento parcial.
    """
    if isinstance(value, datetime):
        return value
    logger.error(
        "[shadow] temporal param inválido (%s=%r type=%s symbol=%s decision_id=%s) "
        "— bloco pulado para evitar 'timestamptz <= interval' (storm 2026-05-19, Task #309)",
        param_name, value, type(value).__name__, symbol, decision_id,
    )
    return None


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
    if not isinstance(row.time, datetime):
        logger.error(
            "[shadow] _next_1m_open: ohlcv.time não-datetime "
            "(type=%s value=%r symbol=%s) — retornando None",
            type(row.time).__name__, row.time, symbol,
        )
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
    if not isinstance(row.time, datetime):
        logger.error(
            "[shadow] _get_current_price_multi_tf: ohlcv.time não-datetime "
            "(type=%s value=%r symbol=%s) — retornando None",
            type(row.time).__name__, row.time, symbol,
        )
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
    if row.last_updated is not None and not isinstance(row.last_updated, datetime):
        logger.error(
            "[shadow] _get_market_metadata_price: last_updated não-datetime "
            "(type=%s value=%r symbol=%s) — retornando ts=None",
            type(row.last_updated).__name__, row.last_updated, symbol,
        )
        return price, None
    return price, row.last_updated


async def _resolve_decision(
    db: AsyncSession,
    user_id,
    symbol: str,
    lookback_minutes: Optional[int] = SHADOW_LOOKBACK_MINUTES,
) -> Optional[DecisionLog]:
    """Mais recente promoção L3 spot (decision='ALLOW' AND direction='SPOT')
    para (user_id, symbol).

    ``lookback_minutes=None`` desativa o filtro de tempo — útil para o
    backfill do monitor, onde o símbolo pode estar em ALLOW estável há
    horas sem nova transição de estado (``_should_log_decision`` só grava
    transições). O ``ON CONFLICT (decision_id) DO NOTHING`` em
    ``_create_from_decision`` garante idempotência mesmo sem janela de tempo.

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
    q = (
        select(DecisionLog)
        .where(
            DecisionLog.user_id == user_id,
            DecisionLog.symbol == symbol,
            DecisionLog.decision == "ALLOW",
            DecisionLog.direction == "SPOT",
        )
        .order_by(DecisionLog.created_at.desc())
        .limit(1)
    )
    if lookback_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
        q = q.where(DecisionLog.created_at >= cutoff)
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

    Macro features (vix_value, btc_dominance, fear_greed_index, etc.) são
    adicionadas pelo pipeline_scan no nível raiz de ``metrics`` (não dentro
    de ``indicators_snapshot``). Esta função também as coleta para garantir
    que cheguem ao dataset do ML.
    """
    from ..ml.macro_features import MACRO_FEATURE_COLUMNS

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
    # Macro features are stored at top-level metrics by pipeline_scan embed block,
    # not inside indicators_snapshot — collect them here so they reach the ML dataset.
    for key in MACRO_FEATURE_COLUMNS:
        if key not in flat and key in metrics:
            flat[key] = metrics[key]
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
    # Defesa contra produtor errôneo (storm Cloud SQL 2026-05-19/20,
    # char 183 do `time <= :t` em ohlcv/funding_rates/decisions_log):
    # asyncpg encoda `timedelta` Python como INTERVAL no Postgres, então
    # `timestamptz <= :param` quebra com ``operator does not exist:
    # timestamp with time zone <= interval``. Sem guard, um único shadow
    # corrompido produz 3 erros raiz + 9 erros de cascata (transação
    # abortada) por ciclo do shadow_trade_monitor (Task #309).
    #
    # Estratégia (Task #309): validar TODOS os parâmetros temporais
    # bound em CADA bloco (`:t` ohlcv, `:t_anchor` ohlcv prev,
    # `:t` funding_rates, `:t` decisions_log). Se algum vier não-datetime,
    # abortamos APENAS o bloco afetado — campos não-afetados continuam
    # sendo enriquecidos (retorno parcial com None nos campos quebrados).
    entry_ts = _validate_temporal_param(
        entry_timestamp,
        param_name="entry_timestamp",
        symbol=symbol,
        decision_id=decision_id,
    )
    if entry_ts is None:
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
            {"s": BTC_CONTEXT_SYMBOL, "t": entry_ts},
        )
        cur_row = res.fetchone()
        if cur_row is not None and cur_row.close is not None:
            cur_close = float(cur_row.close)
            out["btc_price_at_entry"] = cur_close

            # Guard adicional: `cur_row.time` é coluna timestamptz, mas
            # se driver/migration corrompida devolver algo diferente
            # (timedelta, None, str), passar como :t_anchor reintroduz
            # o storm. Validar antes de bind. (Task #309)
            t_anchor = _validate_temporal_param(
                cur_row.time,
                param_name="t_anchor",
                symbol=symbol,
                decision_id=decision_id,
            )
            if t_anchor is not None:
                # Task #309 follow-up: mover a aritmética de intervalo para
                # Python elimina o padrão `:param - interval '...'` do SQL.
                # Esse padrão causa ``operator does not exist: timestamptz <=
                # interval`` porque asyncpg/PostgreSQL pode inferir :t_anchor
                # como INTERVAL (ao invés de TIMESTAMPTZ) durante o PREPARE,
                # mesmo quando o valor Python é um datetime válido — o
                # prepared-statement cache contaminado por uma execução
                # anterior com timedelta perpetua o tipo errado na sessão.
                # Passando datetimes pré-computados, asyncpg envia OID 1184
                # (TIMESTAMPTZ) e o PostgreSQL resolve ``TIMESTAMPTZ <=
                # TIMESTAMPTZ`` sem ambiguidade.
                t_60m_before = t_anchor - timedelta(minutes=60)
                t_135m_before = t_anchor - timedelta(minutes=135)
                prev_res = await db.execute(
                    text(
                        """
                        SELECT close
                          FROM ohlcv
                         WHERE symbol = :s
                           AND timeframe = '1h'
                           AND time <= :t_60m_before
                           AND time >= :t_135m_before
                         ORDER BY time DESC
                         LIMIT 1
                        """
                    ),
                    {"s": BTC_CONTEXT_SYMBOL, "t_60m_before": t_60m_before, "t_135m_before": t_135m_before},
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
            symbol, entry_ts,
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
            {"s": symbol, "t": entry_ts},
        )
        row = res.fetchone()
        if row is not None and row.rate is not None:
            out["funding_rate_at_entry"] = float(row.rate)
    except Exception:
        logger.exception(
            "[shadow] enrich_market_context: funding_rates lookup failed "
            "(symbol=%s entry_ts=%s)",
            symbol, entry_ts,
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
            {"t": entry_ts},
        )
        row = res.fetchone()
        if row is not None and row.n is not None:
            out["n_concurrent_signals"] = int(row.n)
    except Exception:
        logger.exception(
            "[shadow] enrich_market_context: concurrent_signals lookup failed "
            "(symbol=%s entry_ts=%s decision_id=%s)",
            symbol, entry_ts, decision_id,
        )

    return out


# ── core: criação a partir de uma DecisionLog ────────────────────────────────

_INSERT_SHADOW_SQL = text("""
    INSERT INTO shadow_trades (
        id,
        decision_id, user_id, symbol, strategy, direction,
        amount_usdt, entry_price, entry_timestamp,
        tp_price, sl_price, tp_pct, sl_pct, timeout_candles,
        status, skip_reason, source, config_snapshot, features_snapshot,
        last_processed_time,
        ttt_enabled, ttt_tp_pct, ttt_timeout_minutes,
        barrier_mode, tp_pct_applied, sl_pct_applied
    ) VALUES (
        gen_random_uuid(),
        :decision_id, :user_id, :symbol, :strategy, :direction,
        :amount_usdt, :entry_price, :entry_timestamp,
        :tp_price, :sl_price, :tp_pct, :sl_pct, :timeout_candles,
        :status, :skip_reason, :source,
        CAST(:config_snapshot AS JSONB),
        CAST(:features_snapshot AS JSONB),
        :last_processed_time,
        :ttt_enabled, :ttt_tp_pct, :ttt_timeout_minutes,
        :barrier_mode, :tp_pct_applied, :sl_pct_applied
    )
    ON CONFLICT (user_id, symbol, source) WHERE status = 'RUNNING' DO NOTHING
    RETURNING id
""")


async def _create_from_decision(
    db: AsyncSession,
    decision: DecisionLog,
    skip_reason: str,
    user_config: Dict[str, Any],
    source: str = SHADOW_SOURCE_L3,
    extra_config: Optional[Dict[str, Any]] = None,
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
    # TTT policy snapshot — lê do user_config (propagado do caller) ou usa defaults.
    ttt_enabled = bool(user_config.get("ttt_enabled", TTT_ENABLED_DEFAULT))
    ttt_tp_pct = float(user_config.get("ttt_tp_pct") or TTT_TP_PCT_DEFAULT)
    ttt_timeout_minutes = int(user_config.get("ttt_timeout_minutes") or TTT_TIMEOUT_MINUTES_DEFAULT)
    # Fase 3 (migration 071) — barreira mode e pcts aplicados (snapshot na abertura).
    barrier_mode = user_config.get("shadow_barrier_mode", "FIXED")

    # P0-1: ATR_DYNAMIC — SL computed per-asset from atr_percent at entry.
    # TP overridden by shadow_tp_pct when set (reads risk.take_profit_pct via DB update).
    if barrier_mode == "ATR_DYNAMIC":
        _feats_early = _build_features_snapshot(decision)
        _atr_pct = float(_feats_early.get("atr_percent") or 0.0)
        if _atr_pct > 0.0:
            _sl_mult = float(user_config.get("sl_atr_multiplier", 1.5))
            _min_sl  = float(user_config.get("sl_min_pct", 0.5))
            _max_sl  = float(user_config.get("sl_max_pct", 3.0))
            sl_pct = max(_min_sl, min(_max_sl, _atr_pct * _sl_mult))
        _shadow_tp = user_config.get("shadow_tp_pct")
        if _shadow_tp:
            tp_pct = float(_shadow_tp)

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
        # TTT policy snapshot (migration 065)
        "ttt_enabled": ttt_enabled,
        "ttt_tp_pct": ttt_tp_pct,
        "ttt_timeout_minutes": ttt_timeout_minutes,
        # ML fee key: monitor reads this to compute net_return_pct at close (B1 fix)
        "ml_fee_roundtrip_pct": user_config.get("ml_fee_roundtrip_pct"),
    }
    # Merge caller-provided metadata (e.g. l3_decision, l3_score, l3_reasons for
    # L3_REJECTED / L3_SIMULATED) into config_snapshot so outcomes can be correlated
    # with gate labels after closure.
    if extra_config:
        config_snap.update(extra_config)
    features_snap = _build_features_snapshot(decision)

    # Task #303: ``decision.id`` é ``None`` para ``_SyntheticDecision``
    # (fallback "live_l3") — vira NULL na coluna após migration 057.
    res = await db.execute(
        _INSERT_SHADOW_SQL,
        {
            "decision_id": getattr(decision, "id", None),
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
            # TTT policy snapshot (migration 065)
            "ttt_enabled": ttt_enabled,
            "ttt_tp_pct": ttt_tp_pct,
            "ttt_timeout_minutes": ttt_timeout_minutes,
            # Fase 3 barrier metadata (migration 071)
            "barrier_mode": barrier_mode,
            "tp_pct_applied": tp_pct or None,
            "sl_pct_applied": sl_pct or None,
            "status": initial_status,
            # skip_reason intencionalmente NULL: textos como NOT_TRADABLE/COOLDOWN
            # poluem o dataset do XGBoost (categórica de alta cardinalidade e
            # correlacionada ao desfecho de forma espúria — pré-execução, não pós).
            # Mantemos a coluna para compat de schema; o motivo do skip continua
            # logado em decisions_log/INFO logs para debugging operacional.
            "skip_reason": None,
            # Task #321: origem da promoção (L3 canônico vs ArrowL1
            # custom). Default 'L3' preserva back-compat com os
            # callers existentes (``safe_create_from_symbol_skip``,
            # ``safe_bulk_create_from_user_skip``).
            "source": source if source in _VALID_SHADOW_SOURCES else SHADOW_SOURCE_L3,
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
    from ..database import CeleryAsyncSessionLocal

    try:
        async with CeleryAsyncSessionLocal() as own_db:
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
    from ..database import CeleryAsyncSessionLocal

    # B2: load ml_fee_roundtrip_pct so _finalize_outcome can compute net_return_pct.
    # Identical pattern to create_shadows_for_new_decisions (lines 1029–1038 + 1059).
    _skip_ml_fee: Any = None
    try:
        from ..models.config_profile import ConfigProfile
        async with CeleryAsyncSessionLocal() as _skp_db:
            _skp_res = await _skp_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            _skp_row = _skp_res.scalar_one_or_none()
            if _skp_row and isinstance(_skp_row.config_json, dict):
                _skip_ml_fee = _skp_row.config_json.get("ml_fee_roundtrip_pct")
    except Exception:
        logger.warning("[shadow] bulk_skip: ml fee load failed user=%s", user_id)
    user_config = {**user_config, "ml_fee_roundtrip_pct": _skip_ml_fee}

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    created_count = 0
    try:
        async with CeleryAsyncSessionLocal() as own_db:
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


async def get_currently_approved_l3(
    db: AsyncSession,
    user_id,
    direction: str = "SPOT",
) -> List[Dict[str, Any]]:
    """Snapshot vivo dos símbolos atualmente aprovados em L3 (Task #303).

    Fonte canônica da L3 "currently approved" — lê
    ``pipeline_watchlist_assets`` (atualizado a cada ciclo do
    ``pipeline_scan``, mesma tabela que serve o botão "Currently Approved
    (L3)" no Decision Log via ``/decisions/approved-snapshot`` e a aba L3
    do diagnostics page via ``/api/diagnostics/l3-queue``).

    Cada item retorna no formato:
        {
            "symbol":              str,
            "score":               float | None,
            "direction":           "SPOT" | "LONG" | "SHORT" | None,
            "approved_at":         datetime | None,
            "watchlist_id":        UUID,
            "watchlist_name":      str,
            "indicators_snapshot": dict[str, float|None]  # FLAT, contrato Task #290
        }

    ``direction='SPOT'`` (default) escopo o sweep ao Shadow Portfolio
    (spot-only por design — Task #292). Para futures, passar ``LONG`` /
    ``SHORT`` e cuidar do ``market_mode='futures'`` upstream.
    """
    return await _get_currently_approved(
        db, user_id, direction=direction
    )


async def _get_currently_approved(
    db: AsyncSession,
    user_id,
    *,
    direction: str = "SPOT",
) -> List[Dict[str, Any]]:
    """Backend para ``get_currently_approved_l3``: retorna ativos aprovados em L3."""
    direction_norm = (direction or "SPOT").upper()
    market_mode = "futures" if direction_norm in {"LONG", "SHORT"} else "spot"

    watchlist_predicate = "UPPER(pw.level) = 'L3'"
    params: Dict[str, Any] = {"uid": str(user_id), "market_mode": market_mode}

    sql = text(
        f"""
        SELECT pwa.symbol,
               pwa.alpha_score              AS alpha,
               pwa.score_long               AS score_long,
               pwa.score_short              AS score_short,
               pwa.futures_direction        AS futures_direction,
               pwa.refreshed_at             AS approved_at,
               pwa.entered_at               AS entered_at,
               pwa.analysis_snapshot        AS analysis_snapshot,
               pw.id                        AS watchlist_id,
               pw.name                      AS watchlist_name,
               pw.market_mode               AS market_mode
          FROM pipeline_watchlist_assets pwa
          JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
         WHERE pw.user_id = :uid
           AND {watchlist_predicate}
           AND LOWER(pw.market_mode) = :market_mode
           AND (pwa.level_direction IS NULL OR pwa.level_direction = 'up')
         ORDER BY pwa.symbol ASC
        """
    )
    res = await db.execute(sql, params)
    rows = res.fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        # Direction canônica (Task #292): SPOT para watchlists spot; para
        # futures escolhe ``futures_direction`` (LONG/SHORT/NEUTRAL).
        if market_mode == "spot":
            row_direction = "SPOT"
        else:
            raw = (r.futures_direction or "").upper()
            row_direction = raw if raw in {"LONG", "SHORT", "NEUTRAL"} else None

        # Filtra pela direção pedida (em spot só tem SPOT mesmo, mas o
        # contrato fica simétrico para futures).
        if direction_norm == "SPOT" and row_direction != "SPOT":
            continue
        if direction_norm in {"LONG", "SHORT"} and row_direction != direction_norm:
            continue

        # Score canônico: alpha para spot, score_long/score_short para futures.
        if direction_norm == "LONG":
            score = r.score_long
        elif direction_norm == "SHORT":
            score = r.score_short
        else:
            score = r.alpha

        out.append({
            "symbol": r.symbol,
            "score": float(score) if score is not None else None,
            "direction": row_direction,
            "approved_at": r.approved_at or r.entered_at,
            "watchlist_id": r.watchlist_id,
            "watchlist_name": r.watchlist_name,
            "indicators_snapshot": _flatten_analysis_snapshot(r.analysis_snapshot),
        })
    return out


def _flatten_analysis_snapshot(
    analysis_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Achata ``pipeline_watchlist_assets.analysis_snapshot`` para o formato
    FLAT consumido pelo ``DatasetBuilder`` (contrato Task #290).

    O ``analysis_snapshot`` traz uma lista de dicts em
    ``["indicators"]`` ou ``["details"]["indicators"]`` com entradas no
    formato ``{"key": "rsi", "value": 42.5, ...}``. Extraímos pares
    ``{key: value}`` ignorando entradas sem ``key`` ou ``value`` válido.
    Compatível com o ``_build_features_snapshot`` legado: chave intermediária
    ``"indicators_snapshot"`` envelopa cada valor em ``{"value": v}``.
    """
    if not isinstance(analysis_snapshot, dict):
        return {}
    details = analysis_snapshot.get("details") if isinstance(
        analysis_snapshot.get("details"), dict
    ) else {}
    ind_list = (
        details.get("indicators")
        or analysis_snapshot.get("indicators")
        or []
    )
    if not isinstance(ind_list, list):
        return {}
    flat: Dict[str, Any] = {}
    for entry in ind_list:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key") or entry.get("indicator") or entry.get("name")
        if not key:
            continue
        val = entry.get("value")
        # Aceita escalar (int/float/bool/None); descarta dict/list para não
        # contaminar o dataset.
        if isinstance(val, (dict, list)):
            continue
        flat[str(key)] = val
    return flat


async def create_shadows_for_new_decisions(user_id, decision_ids: List[int]) -> int:
    """Inline shadow creation triggered by pipeline_scan immediately after
    _persist_decision_logs.

    Eliminates the async gap between the decisions_log commit and the next
    shadow_trade_monitor beat (up to 5 min), which is the primary source of
    orphaned ALLOW decisions reported by the P0 pipeline-integrity probe.

    Strategy:
    * Loads the user's active spot_engine ConfigProfile once (tp_pct / sl_pct).
    * Opens one own-session per decision_id (deadlock-safe, same as backfill).
    * Calls _create_from_decision which is idempotent via ON CONFLICT
      (decision_id) DO NOTHING — safe to call even if the monitor beat runs
      concurrently and creates the same shadow first.

    Fire-and-forget: never raises. Returns count of new shadows created.
    """
    if not decision_ids:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    # Load user config once; bail out silently if config is missing.
    user_config: Dict[str, Any] = {}
    _ml_config: Dict[str, Any] = {}
    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            cfg_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            cfg_row = cfg_res.scalar_one_or_none()
            if cfg_row:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                user_config = {
                    "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(se_cfg.selling.take_profit_pct),
                    "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                        se_cfg.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
            # Load ML config for fee snapshot (B1 fix)
            ml_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            ml_row = ml_res.scalar_one_or_none()
            if ml_row and isinstance(ml_row.config_json, dict):
                _ml_config = ml_row.config_json
    except Exception:
        logger.exception(
            "[shadow] inline create: config load failed for user=%s", user_id
        )
        return 0

    if not user_config:
        _se_defaults = SpotEngineConfig()
        user_config = {
            "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se_defaults.selling.take_profit_pct),
            "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                _se_defaults.sell_flow.kill_switch.max_drawdown_from_hwm_pct
            ),
            "timeout_candles": None,
        }
        logger.info(
            "[shadow] inline create: no spot_engine config for user=%s — using schema defaults (tp=%.1f%% sl=%.1f%%)",
            user_id, user_config["tp_pct"], user_config["sl_pct"],
        )

    _apply_barrier_params(user_config, _ml_config)
    user_config["ml_fee_roundtrip_pct"] = _ml_config.get("ml_fee_roundtrip_pct")

    created = 0
    # Sorted for deadlock safety (same convention as safe_backfill — gotcha #251/#273).
    for decision_id in sorted(decision_ids):
        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    res = await own_db.execute(
                        select(DecisionLog).where(DecisionLog.id == decision_id)
                    )
                    decision = res.scalar_one_or_none()
                    if decision is None:
                        # Shouldn't happen — the pipeline_scan just committed
                        # this row — but guard against any race.
                        logger.debug(
                            "[shadow] inline create: decision_id=%s not found",
                            decision_id,
                        )
                        continue
                    new_id = await _create_from_decision(
                        own_db, decision, "NOT_TRADABLE", user_config,
                        source=SHADOW_SOURCE_L3,
                    )
                    if new_id is not None:
                        created += 1
                        logger.info(
                            "[shadow] inline created id=%s symbol=%s decision_id=%s",
                            new_id, decision.symbol, decision_id,
                        )
        except Exception:
            logger.exception(
                "[shadow] inline create failed for decision_id=%s user=%s",
                decision_id, user_id,
            )

    if created:
        logger.info(
            "[shadow] inline create: %d shadow(s) created inline for user=%s",
            created, user_id,
        )
    return created


async def create_shadows_for_rejected_decisions(user_id, decision_ids: List[int]) -> int:
    """Inline shadow creation for L3-rejected decisions (ML data collection).

    Mirror of ``create_shadows_for_new_decisions`` for BLOCK decisions.
    Simulates "what would have happened" if the rejected asset had been
    traded, giving the ML model negative examples with full indicator context.

    Key differences from approved-shadow flow:
    * ``source = 'L3_REJECTED'`` — segregated from approved shadows so
      metrics, P&L dashboards, and the ML DatasetBuilder can filter them
      independently. Never contaminate approved-asset statistics.
    * Uses ``ON CONFLICT (user_id, symbol) WHERE status = 'RUNNING' DO NOTHING``
      (same as approved) — if the same symbol already has a RUNNING shadow
      (from an approved decision in the same cycle), the rejected shadow is
      skipped. ML data from the approved period already covers that window.

    Fire-and-forget: never raises. Returns count of new shadows created.
    """
    if not decision_ids:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    user_config: Dict[str, Any] = {}
    _ml_config: Dict[str, Any] = {}
    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            cfg_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            cfg_row = cfg_res.scalar_one_or_none()
            if cfg_row:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                user_config = {
                    "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(se_cfg.selling.take_profit_pct),
                    "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                        se_cfg.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
            # Load ML config for fee snapshot (B1 fix)
            ml_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            ml_row = ml_res.scalar_one_or_none()
            if ml_row and isinstance(ml_row.config_json, dict):
                _ml_config = ml_row.config_json
    except Exception:
        logger.exception(
            "[shadow] rejected create: config load failed for user=%s", user_id
        )
        return 0

    if not user_config:
        _se_defaults = SpotEngineConfig()
        user_config = {
            "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se_defaults.selling.take_profit_pct),
            "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                _se_defaults.sell_flow.kill_switch.max_drawdown_from_hwm_pct
            ),
            "timeout_candles": None,
        }
        logger.info(
            "[shadow] rejected create: no spot_engine config for user=%s — using schema defaults (tp=%.1f%% sl=%.1f%%)",
            user_id, user_config["tp_pct"], user_config["sl_pct"],
        )

    _apply_barrier_params(user_config, _ml_config)
    user_config["ml_fee_roundtrip_pct"] = _ml_config.get("ml_fee_roundtrip_pct")

    created = 0
    for decision_id in sorted(decision_ids):
        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    res = await own_db.execute(
                        select(DecisionLog).where(DecisionLog.id == decision_id)
                    )
                    decision = res.scalar_one_or_none()
                    if decision is None:
                        logger.debug(
                            "[shadow] rejected create: decision_id=%s not found",
                            decision_id,
                        )
                        continue
                    new_id = await _create_from_decision(
                        own_db, decision, "L3_REJECTED", user_config,
                        source=SHADOW_SOURCE_L3_REJECTED,
                    )
                    if new_id is not None:
                        created += 1
                        logger.info(
                            "[shadow] rejected created id=%s symbol=%s decision_id=%s",
                            new_id, decision.symbol, decision_id,
                        )
        except Exception:
            logger.exception(
                "[shadow] rejected create failed for decision_id=%s user=%s",
                decision_id, user_id,
            )

    if created:
        logger.info(
            "[shadow] rejected create: %d shadow(s) created for user=%s",
            created, user_id,
        )
    return created


async def create_l3_rejected_inline_shadows(
    user_id,
    decisions: List[Dict[str, Any]],
    execution_id: str,
    promotion_at: "datetime",
    profile_id: Optional[Any] = None,
    profile_version: Optional[datetime] = None,
    profile_name: Optional[str] = None,
) -> int:
    """L3_REJECTED inline capture: shadow para TODOS os ativos com decision=BLOCK no ciclo L3.

    Diferente de ``create_shadows_for_rejected_decisions`` (que depende de decision_ids
    da decisions_log), esta função opera diretamente sobre a lista ``decisions`` do ciclo,
    criando shadows sintéticos (decision_id=NULL) para TODOS os ativos BLOCK — não apenas
    os que dispararam edge-trigger em decisions_log.

    Por quê: ``_should_log_decision`` é edge-triggered (só loga mudanças de estado).
    Ativos estáveis em BLOCK por dias nunca entram em decision_payloads, fazendo
    L3_REJECTED ficar permanentemente vazio com a abordagem via IDs.

    ON CONFLICT (user_id, symbol, source) WHERE status='RUNNING' DO NOTHING garante
    que ciclos repetidos para o mesmo símbolo em BLOCK não criam duplicatas.

    Always-on: sem feature flag (espelha o comportamento ALLOW). Rate-limited por
    ML config: shadow_capture_l3_rejected_max_per_hour (default 500).

    Fire-and-forget: nunca levanta exceção. Retorna count de shadows criados.
    """
    if not decisions:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    ml_config: Dict[str, Any] = {}
    user_config: Dict[str, Any] = {}
    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            ml_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            ml_row = ml_res.scalar_one_or_none()
            if ml_row and isinstance(ml_row.config_json, dict):
                ml_config = ml_row.config_json

            se_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            se_row = se_res.scalar_one_or_none()
            if se_row:
                _se = SpotEngineConfig.from_config_json(se_row.config_json)
                user_config = {
                    "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se.selling.take_profit_pct),
                    "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                        _se.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
    except Exception:
        logger.exception("[shadow-l3rej-inline] config load failed user=%s", user_id)
        return 0

    max_per_hour = int(ml_config.get("shadow_capture_l3_rejected_max_per_hour", 500))

    if not user_config:
        _se_defaults = SpotEngineConfig()
        user_config = {
            "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se_defaults.selling.take_profit_pct),
            "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                _se_defaults.sell_flow.kill_switch.max_drawdown_from_hwm_pct
            ),
            "timeout_candles": None,
        }
    _apply_barrier_params(user_config, ml_config)
    user_config["ml_fee_roundtrip_pct"] = ml_config.get("ml_fee_roundtrip_pct")

    shadows_last_hour = 0
    try:
        async with CeleryAsyncSessionLocal() as count_db:
            cnt_res = await count_db.execute(
                text("""
                    SELECT COUNT(*) FROM shadow_trades
                    WHERE user_id = :uid
                      AND source = :src
                      AND created_at > NOW() - INTERVAL '1 hour'
                """),
                {"uid": str(user_id), "src": SHADOW_SOURCE_L3_REJECTED},
            )
            shadows_last_hour = cnt_res.scalar_one() or 0
    except Exception:
        logger.warning(
            "[shadow-l3rej-inline] rate limit count failed user=%s — skipping", user_id
        )
        return 0

    created = 0
    rate_limited = 0

    for d in decisions:
        if shadows_last_hour + created >= max_per_hour:
            rate_limited += 1
            continue

        symbol = d.get("symbol")
        if not symbol:
            continue

        metrics: Dict[str, Any] = dict(d.get("metrics") or {})
        metrics["l3_decision"] = "BLOCK"
        metrics["l3_score"] = d.get("score")
        metrics["l3_reasons"] = d.get("reasons")
        metrics["source"] = "l3_rejected_inline"
        metrics["execution_id"] = execution_id
        _asset = d.get("_asset") or {}
        if isinstance(_asset, dict):
            _price = _asset.get("current_price") or _asset.get("price")
            if _price is not None:
                metrics.setdefault("current_price", _price)

        synthetic = _SyntheticDecision(
            user_id=user_id,
            symbol=symbol,
            direction=d.get("direction", "SPOT"),
            strategy=d.get("strategy"),
            id=None,
            created_at=promotion_at,
            metrics=metrics,
        )

        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    new_id = await _create_from_decision(
                        own_db, synthetic, "L3_REJECTED_INLINE", user_config,
                        source=SHADOW_SOURCE_L3_REJECTED,
                        extra_config={
                            "l3_decision": "BLOCK",
                            "l3_score": d.get("score"),
                            "l3_reasons": d.get("reasons"),
                        },
                    )
                    if new_id is not None:
                        created += 1
                        logger.debug(
                            "[shadow-l3rej-inline] created id=%s symbol=%s user=%s",
                            new_id, symbol, user_id,
                        )
        except Exception:
            logger.exception(
                "[shadow-l3rej-inline] create failed symbol=%s user=%s", symbol, user_id
            )

    if created or rate_limited:
        logger.info(
            "[shadow-l3rej-inline] cycle done: created=%d rate_limited=%d block_decisions=%d user=%s",
            created, rate_limited, len(decisions), user_id,
        )
    return created


async def create_l1_spectrum_shadows(
    user_id,
    symbols: List[str],
    execution_id: str,
    assets_by_symbol: Dict[str, Dict[str, Any]],
    promotion_at: "datetime",
) -> int:
    """L1_SPECTRUM capture: create sampled shadow trades from L1 stage promotions.

    Called inline by pipeline_scan after _upsert_assets for L1 watchlists.
    Implements deterministic hash sampling, per-source reentry policy,
    rate limiting, and skip logging — all from config_type='ml' config.

    Pureza invariant: zero quality conditionals between L1 promotion and
    shadow creation. Only structural discards: sampling, reentry policy,
    rate limit. Each discard is recorded in shadow_capture_skips.

    Fire-and-forget: never raises. Returns count of new shadows created.
    """
    import hashlib

    if not symbols:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    # 1. Load ML + spot_engine configs in one session
    ml_config: Dict[str, Any] = {}
    user_config: Dict[str, Any] = {}
    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            ml_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            ml_row = ml_res.scalar_one_or_none()
            if ml_row and isinstance(ml_row.config_json, dict):
                ml_config = ml_row.config_json

            se_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            se_row = se_res.scalar_one_or_none()
            if se_row:
                _se = SpotEngineConfig.from_config_json(se_row.config_json)
                user_config = {
                    "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se.selling.take_profit_pct),
                    "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                        _se.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
    except Exception:
        logger.exception("[shadow-l1] config load failed user=%s", user_id)
        return 0

    if not ml_config.get("shadow_capture_l1_enabled", False):
        return 0

    sample_rate = float(ml_config.get("shadow_capture_l1_sample_rate", 0.10))
    source_label = str(ml_config.get("shadow_capture_l1_source_label", SHADOW_SOURCE_L1_SPECTRUM))
    if source_label not in _VALID_SHADOW_SOURCES:
        source_label = SHADOW_SOURCE_L1_SPECTRUM
    max_per_hour = int(ml_config.get("shadow_capture_l1_max_per_hour", 200))
    skip_log_enabled = bool(ml_config.get("shadow_skip_log_enabled", True))

    if not user_config:
        _se_defaults = SpotEngineConfig()
        user_config = {
            "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se_defaults.selling.take_profit_pct),
            "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                _se_defaults.sell_flow.kill_switch.max_drawdown_from_hwm_pct
            ),
            "timeout_candles": None,
        }
    _apply_barrier_params(user_config, ml_config)
    user_config["ml_fee_roundtrip_pct"] = ml_config.get("ml_fee_roundtrip_pct")

    # 2. Rate limit: count shadows from this source in the last hour
    shadows_last_hour = 0
    try:
        async with CeleryAsyncSessionLocal() as count_db:
            cnt_res = await count_db.execute(
                text("""
                    SELECT COUNT(*) FROM shadow_trades
                    WHERE user_id = :uid
                      AND source = :src
                      AND created_at > NOW() - INTERVAL '1 hour'
                """),
                {"uid": str(user_id), "src": source_label},
            )
            shadows_last_hour = cnt_res.scalar_one() or 0
    except Exception:
        logger.warning(
            "[shadow-l1] rate limit count failed user=%s — skipping cycle", user_id
        )
        return 0

    # 3. Deterministic hash sampling: hash(symbol:execution_id) % 10000 < rate*10000
    #    Same input → same decision; reproducible in audit, uniform, quality-agnostic.
    sampled: List[str] = []
    sampled_out: List[str] = []
    for symbol in sorted(symbols):
        _h = int(hashlib.sha256(f"{symbol}:{execution_id}".encode()).hexdigest(), 16) % 10000
        if _h < int(sample_rate * 10000):
            sampled.append(symbol)
        else:
            sampled_out.append(symbol)

    # Log SAMPLED_OUT aggregated (one row per cycle, not per symbol — avoids write volume)
    if sampled_out and skip_log_enabled:
        try:
            async with CeleryAsyncSessionLocal() as skip_db:
                async with skip_db.begin():
                    await skip_db.execute(
                        text("""
                            INSERT INTO shadow_capture_skips
                                (user_id, symbol, promotion_at, skip_reason, source_path)
                            VALUES (:uid, :sym, :ts, 'SAMPLED_OUT', :src)
                        """),
                        {
                            "uid": str(user_id),
                            "sym": f"[{len(sampled_out)} symbols]",
                            "ts": promotion_at,
                            "src": source_label,
                        },
                    )
        except Exception:
            logger.warning("[shadow-l1] SAMPLED_OUT skip log failed user=%s", user_id)

    if not sampled:
        return 0

    # 4. Create shadows for sampled symbols
    created = 0
    rate_limited = 0

    # B1 — bulk indicators fetch before loop (L1 assets have no analysis_snapshot).
    # Pureza invariant: low coverage is RECORDED, never a reason to skip shadow creation.
    _ind_captured_at = promotion_at.isoformat()
    _expected_n_features = 37
    features_by_symbol: Dict[str, Dict[str, Any]] = {}
    try:
        from .indicators_provider import get_merged_indicators
        async with CeleryAsyncSessionLocal() as _ind_db:
            _merged = await get_merged_indicators(_ind_db, sampled, include_stale=True)
        _now_utc = datetime.now(timezone.utc)
        for _sym in sampled:
            _mi = _merged.get(_sym)
            if _mi is not None:
                _flat = _mi.as_flat_dict()
                _n_cap = sum(1 for v in _flat.values() if v is not None)
                _coverage = _n_cap / max(_expected_n_features, 1)
                _oldest_age_s = None
                try:
                    _ts_list = [
                        _m.get("timestamp")
                        for _m in _mi.meta.values()
                        if _m.get("timestamp") is not None
                    ]
                    if _ts_list:
                        _oldest_ts = min(
                            t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                            for t in _ts_list
                        )
                        _oldest_age_s = int((_now_utc - _oldest_ts).total_seconds())
                except Exception:
                    pass
                features_by_symbol[_sym] = {
                    **{k: {"value": v} for k, v in _flat.items()},
                    "_features_captured_at":   {"value": _ind_captured_at},
                    "_features_coverage":      {"value": round(_coverage, 3)},
                    "_oldest_indicator_age_s": {"value": _oldest_age_s},
                }
            else:
                features_by_symbol[_sym] = {
                    "_features_captured_at":   {"value": _ind_captured_at},
                    "_features_coverage":      {"value": 0.0},
                    "_oldest_indicator_age_s": {"value": None},
                }
    except Exception as _b1_err:
        logger.warning(
            "[shadow-l1] B1: indicators fetch failed (%s) — shadows created with empty features",
            _b1_err,
        )
        features_by_symbol = {}

    for symbol in sampled:
        if shadows_last_hour + created >= max_per_hour:
            # Hard rate ceiling hit — log per symbol (structural discard, not quality)
            rate_limited += 1
            if skip_log_enabled:
                try:
                    async with CeleryAsyncSessionLocal() as skip_db:
                        async with skip_db.begin():
                            await skip_db.execute(
                                text("""
                                    INSERT INTO shadow_capture_skips
                                        (user_id, symbol, promotion_at, skip_reason, source_path)
                                    VALUES (:uid, :sym, :ts, 'RATE_LIMITED', :src)
                                """),
                                {
                                    "uid": str(user_id), "sym": symbol,
                                    "ts": promotion_at, "src": source_label,
                                },
                            )
                except Exception:
                    pass
            continue

        # B1: use pre-fetched live indicators; fallback to empty dict on cache miss.
        asset = assets_by_symbol.get(symbol, {})
        _sym_features = features_by_symbol.get(symbol, {
            "_features_captured_at":   {"value": _ind_captured_at},
            "_features_coverage":      {"value": 0.0},
            "_oldest_indicator_age_s": {"value": None},
        })
        metrics = {
            "indicators_snapshot": _sym_features,
            "source": "l1_spectrum_inline",
            "current_price": asset.get("current_price") or asset.get("price"),
        }
        synthetic = _SyntheticDecision(
            user_id=user_id,
            symbol=symbol,
            direction="SPOT",
            strategy=None,
            id=None,
            created_at=promotion_at,
            metrics=metrics,
        )

        _scoring_id = None
        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    new_id = await _create_from_decision(
                        own_db, synthetic, "L1_SPECTRUM_CAPTURE", user_config,
                        source=source_label,
                    )
                    if new_id is not None:
                        created += 1
                        _scoring_id = new_id
                        logger.debug(
                            "[shadow-l1] created id=%s symbol=%s user=%s",
                            new_id, symbol, user_id,
                        )
                    elif skip_log_enabled:
                        # ON CONFLICT (user_id, symbol, source) → RUNNING_DUPLICATE
                        # Must log unitarily: required for ML sample weights (López de Prado)
                        await own_db.execute(
                            text("""
                                INSERT INTO shadow_capture_skips
                                    (user_id, symbol, promotion_at, skip_reason, source_path)
                                VALUES (:uid, :sym, :ts, 'RUNNING_DUPLICATE', :src)
                            """),
                            {
                                "uid": str(user_id), "sym": symbol,
                                "ts": promotion_at, "src": source_label,
                            },
                        )
        except Exception:
            logger.exception(
                "[shadow-l1] create failed symbol=%s user=%s", symbol, user_id
            )

        # Forward scoring — own session, outside shadow creation transaction.
        # Passive write to ml_predictions; never influences any decision path.
        if _scoring_id is not None:
            try:
                from ..ml.forward_scorer import safe_score_shadow_trade as _fwd_score
                await _fwd_score(
                    _scoring_id,
                    _build_features_snapshot(synthetic),
                    symbol,
                )
            except Exception as _fwd_exc:
                logger.debug("[shadow-l1] forward scorer skipped: %s", _fwd_exc)

    if created or rate_limited:
        logger.info(
            "[shadow-l1] cycle done: created=%d rate_limited=%d sampled=%d eligible=%d user=%s",
            created, rate_limited, len(sampled), len(symbols), user_id,
        )
    return created


async def create_l3_simulated_shadows(
    user_id,
    decisions: List[Dict[str, Any]],
    execution_id: str,
    promotion_at: "datetime",
    profile_id: Optional[Any] = None,
    profile_version: Optional[datetime] = None,
    profile_name: Optional[str] = None,
) -> int:
    """L3_SIMULATED capture: camada contrafactual para TODOS os ativos avaliados no gate L3.

    Chamado inline pelo pipeline_scan para TODOS os ativos que chegaram à decisão L3,
    independente de ALLOW ou BLOCK. Permite análise contrafactual: "o que teria acontecido
    se este ativo tivesse sido operado independente da decisão do filtro L3?"

    Design:
    * source = 'L3_SIMULATED' — segregado de L3 (ALLOW real) e L3_REJECTED (BLOCK real)
    * Sem sampling (100% captura — completude contrafactual)
    * decision_id = NULL — sintético, não vinculado a uma linha decisions_log
    * metrics contém l3_decision (ALLOW/BLOCK) para rotulagem contrafactual
    * Controlado por ML config: shadow_capture_l3_simulated_enabled (default False)
    * Rate limit: shadow_capture_l3_simulated_max_per_hour (default 500)

    Fire-and-forget: nunca levanta exceção. Retorna count de shadows criados.
    """
    if not decisions:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    ml_config: Dict[str, Any] = {}
    user_config: Dict[str, Any] = {}
    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            ml_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            ml_row = ml_res.scalar_one_or_none()
            if ml_row and isinstance(ml_row.config_json, dict):
                ml_config = ml_row.config_json

            se_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            se_row = se_res.scalar_one_or_none()
            if se_row:
                _se = SpotEngineConfig.from_config_json(se_row.config_json)
                user_config = {
                    "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se.selling.take_profit_pct),
                    "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                        _se.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
    except Exception:
        logger.exception("[shadow-l3sim] config load failed user=%s", user_id)
        return 0

    if not ml_config.get("shadow_capture_l3_simulated_enabled", False):
        return 0

    max_per_hour = int(ml_config.get("shadow_capture_l3_simulated_max_per_hour", 500))

    if not user_config:
        _se_defaults = SpotEngineConfig()
        user_config = {
            "tp_pct": _SHADOW_TP_PCT_OVERRIDE or float(_se_defaults.selling.take_profit_pct),
            "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                _se_defaults.sell_flow.kill_switch.max_drawdown_from_hwm_pct
            ),
            "timeout_candles": None,
        }
    _apply_barrier_params(user_config, ml_config)
    user_config["ml_fee_roundtrip_pct"] = ml_config.get("ml_fee_roundtrip_pct")

    shadows_last_hour = 0
    try:
        async with CeleryAsyncSessionLocal() as count_db:
            cnt_res = await count_db.execute(
                text("""
                    SELECT COUNT(*) FROM shadow_trades
                    WHERE user_id = :uid
                      AND source = :src
                      AND created_at > NOW() - INTERVAL '1 hour'
                """),
                {"uid": str(user_id), "src": SHADOW_SOURCE_L3_SIMULATED},
            )
            shadows_last_hour = cnt_res.scalar_one() or 0
    except Exception:
        logger.warning(
            "[shadow-l3sim] rate limit count failed user=%s — skipping cycle", user_id
        )
        return 0

    created = 0
    rate_limited = 0

    for d in decisions:
        if shadows_last_hour + created >= max_per_hour:
            rate_limited += 1
            continue

        symbol = d.get("symbol")
        if not symbol:
            continue

        # Mescla métricas L3 existentes + rótulo contrafactual para rastreabilidade
        metrics: Dict[str, Any] = dict(d.get("metrics") or {})
        metrics["l3_decision"] = d.get("decision")  # ALLOW ou BLOCK — label contrafactual
        metrics["l3_score"] = d.get("score")
        metrics["source"] = "l3_simulated_inline"
        metrics["execution_id"] = execution_id
        _asset = d.get("_asset") or {}
        if isinstance(_asset, dict):
            _price = _asset.get("current_price") or _asset.get("price")
            if _price is not None:
                metrics.setdefault("current_price", _price)

        synthetic = _SyntheticDecision(
            user_id=user_id,
            symbol=symbol,
            direction=d.get("direction", "SPOT"),
            strategy=d.get("strategy"),
            id=None,
            created_at=promotion_at,
            metrics=metrics,
        )

        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    new_id = await _create_from_decision(
                        own_db, synthetic, "L3_SIMULATED_CAPTURE", user_config,
                        source=SHADOW_SOURCE_L3_SIMULATED,
                        extra_config={
                            "l3_decision": d.get("decision"),
                            "l3_score": d.get("score"),
                            "l3_reasons": d.get("reasons"),
                        },
                    )
                    if new_id is not None:
                        created += 1
                        logger.debug(
                            "[shadow-l3sim] created id=%s symbol=%s l3_dec=%s user=%s",
                            new_id, symbol, d.get("decision"), user_id,
                        )
        except Exception:
            logger.exception(
                "[shadow-l3sim] create failed symbol=%s user=%s", symbol, user_id
            )

    if created or rate_limited:
        logger.info(
            "[shadow-l3sim] cycle done: created=%d rate_limited=%d total_decisions=%d user=%s",
            created, rate_limited, len(decisions), user_id,
        )
    return created


async def safe_backfill_watchlist_shadows(
    user_id,
    user_config: Dict[str, Any],
) -> int:
    """Cria shadow trades para símbolos aprovados na watchlist sem shadow RUNNING.

    Mecanismo de safety-net acionado pelo ``shadow_trade_monitor`` a cada
    ciclo. Cobre o gap estrutural entre:

    * ``pipeline_scan._should_log_decision`` — só grava em ``decisions_log``
      em transições de estado (BLOCK→ALLOW, ALLOW→BLOCK, ALLOW→ALLOW com
      delta de score). Símbolo em ALLOW estável NÃO gera nova linha.
    * ``_resolve_decision`` com janela de 10 min — retorna None quando a
      decisão mais recente é mais antiga que a janela, impedindo criação
      de shadow mesmo com o símbolo aprovado na watchlist.

    Usa ``_resolve_decision(lookback_minutes=None)`` (sem limite de tempo)
    e ``ON CONFLICT (decision_id) DO NOTHING`` para idempotência: se o
    shadow já existe para aquela decisão, não cria duplicata.

    Fire-and-forget: nunca raise. Retorna o número de shadows criados.
    """
    from ..database import CeleryAsyncSessionLocal

    # B2: load ml_fee_roundtrip_pct so _finalize_outcome can compute net_return_pct.
    # Identical pattern to create_shadows_for_new_decisions (lines 1029–1038 + 1059).
    _backfill_ml_fee: Any = None
    try:
        from ..models.config_profile import ConfigProfile
        async with CeleryAsyncSessionLocal() as _bcf_db:
            _bcf_res = await _bcf_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "ml",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            _bcf_row = _bcf_res.scalar_one_or_none()
            if _bcf_row and isinstance(_bcf_row.config_json, dict):
                _backfill_ml_fee = _bcf_row.config_json.get("ml_fee_roundtrip_pct")
    except Exception:
        logger.warning("[shadow] backfill: ml fee load failed user=%s", user_id)
    user_config = {**user_config, "ml_fee_roundtrip_pct": _backfill_ml_fee}

    # ── Snapshot vivo da origem L3 ───────────────────────────────────────
    snapshot_l3: List[Dict[str, Any]] = []
    running_set: set = set()
    try:
        async with CeleryAsyncSessionLocal() as read_db:
            snapshot_l3 = await get_currently_approved_l3(
                read_db, user_id, direction="SPOT"
            )
            # Tratar PENDING como aberto: shadows sintéticas (decision_id NULL)
            # podem ficar PENDING enquanto o entry_price não resolve. Sem
            # incluir PENDING, ciclos seguintes recriariam linha duplicada
            # (a UNIQUE parcial cobre RUNNING, não PENDING).
            running_rows = await read_db.execute(
                text(
                    """
                    SELECT symbol FROM shadow_trades
                     WHERE user_id = :uid AND status IN ('RUNNING', 'PENDING')
                    """
                ),
                {"uid": str(user_id)},
            )
            running_set = {r.symbol for r in running_rows.fetchall()}
    except Exception:
        logger.exception(
            "[shadow] backfill: snapshot query failed for user=%s", user_id
        )
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=SHADOW_LOOKBACK_MINUTES)
    counts_by_source: Dict[str, int] = {
        "recent_log": 0, "stale_log": 0, "live_l3": 0
    }
    created_count = 0

    snapshot_by_symbol: Dict[str, Dict[str, Any]] = {
        item["symbol"]: item for item in snapshot_l3
    }
    # Ordenação determinística antes do loop UPSERT — gotcha #251/#273/#310
    # (deadlock 40P01 quando dois workers iteram o mesmo set de
    # símbolos em ordens diferentes).
    eligible_symbols = sorted(
        s for s in snapshot_by_symbol.keys() if s not in running_set
    )

    for symbol in eligible_symbols:
        snap_item = snapshot_by_symbol[symbol]
        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    decision, source = await _resolve_decision_with_fallback(
                        own_db, user_id, symbol, snap_item, cutoff
                    )
                    if decision is None:
                        logger.debug(
                            "[shadow] backfill: no resolution path for "
                            "user=%s symbol=%s",
                            user_id, symbol,
                        )
                        continue
                    new_id = await _create_from_decision(
                        own_db, decision, "NOT_TRADABLE", user_config,
                        source=SHADOW_SOURCE_L3,
                    )
                    if new_id is not None:
                        created_count += 1
                        counts_by_source[source] = counts_by_source.get(source, 0) + 1
                        shadow_metrics.record_resolved_source(source)
                        running_set.add(symbol)
                        logger.info(
                            "[shadow] backfill created id=%s symbol=%s "
                            "decision_id=%s source=%s",
                            new_id, symbol,
                            getattr(decision, "id", None), source,
                        )
        except Exception:
            logger.exception(
                "[shadow] backfill failed for user=%s symbol=%s",
                user_id, symbol,
            )
    if created_count:
        logger.info(
            "[shadow] backfill total=%d new shadow(s) for user=%s by_source=%s",
            created_count, user_id, counts_by_source,
        )
    return created_count


async def _resolve_decision_with_fallback(
    db: AsyncSession,
    user_id,
    symbol: str,
    snap_item: Dict[str, Any],
    cutoff: datetime,
) -> tuple[Optional[Any], str]:
    """Resolve uma decisão para promover ao Shadow seguindo a cascata da
    Task #303:

    1. ``recent_log`` — última ``decisions_log`` ALLOW/SPOT dentro de
       ``SHADOW_LOOKBACK_MINUTES`` (default 10 min).
    2. ``stale_log``  — última ALLOW/SPOT sem janela de tempo.
    3. ``live_l3``    — snapshot vivo de ``pipeline_watchlist_assets``
       (símbolo cronicamente aprovado que nunca gerou transição em
       ``decisions_log``).

    Retorna ``(decision, source)`` onde ``decision`` é ``DecisionLog`` ou
    ``_SyntheticDecision``. ``(None, "")`` se nada disponível
    (não deveria acontecer — o caller só chama com símbolos já no
    snapshot, mas mantido por defesa).
    """
    # (a) janela de 10 min
    res = await db.execute(
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
    decision = res.scalar_one_or_none()
    if decision is not None:
        return decision, "recent_log"

    # (b) sem janela
    res = await db.execute(
        select(DecisionLog)
        .where(
            DecisionLog.user_id == user_id,
            DecisionLog.symbol == symbol,
            DecisionLog.decision == "ALLOW",
            DecisionLog.direction == "SPOT",
        )
        .order_by(DecisionLog.created_at.desc())
        .limit(1)
    )
    decision = res.scalar_one_or_none()
    if decision is not None:
        return decision, "stale_log"

    # (c) snapshot vivo da L3
    if snap_item is None:
        return None, ""
    flat = snap_item.get("indicators_snapshot") or {}
    # Envelopa em ``{"indicators_snapshot": {k: {"value": v}}}`` para
    # reusar ``_build_features_snapshot`` sem ramificação adicional.
    metrics = {
        "indicators_snapshot": {k: {"value": v} for k, v in flat.items()},
        "source": "live_l3_snapshot",
        "score": snap_item.get("score"),
        "watchlist_id": (
            str(snap_item["watchlist_id"])
            if snap_item.get("watchlist_id") is not None else None
        ),
    }
    synthetic = _SyntheticDecision(
        user_id=user_id,
        symbol=symbol,
        direction=snap_item.get("direction") or "SPOT",
        strategy=None,
        id=None,
        created_at=snap_item.get("approved_at") or datetime.now(timezone.utc),
        metrics=metrics,
    )
    return synthetic, "live_l3"


_INSERT_SIM_SQL = text("""
    INSERT INTO trade_simulations (
        id, symbol, timestamp_entry, entry_price,
        tp_price, sl_price,
        exit_price, exit_timestamp,
        result, time_to_result,
        direction, is_simulated, source,
        decision_type, decision_id,
        features_snapshot, config_snapshot,
        created_at,
        mae_at, mfe_at,
        barrier_touched, barrier_touched_at, intrabar_convention,
        final_return_pct, net_return_pct, fee_roundtrip_pct_applied,
        barrier_mode, tp_pct_applied, sl_pct_applied, atr_pct_at_entry,
        min_price_post_entry, max_price_post_entry,
        max_drawdown_pct, max_profit_pct,
        mae_pct, mfe_pct,
        exit_metrics_json
    ) VALUES (
        gen_random_uuid(), :symbol, :timestamp_entry, :entry_price,
        :tp_price, :sl_price,
        :exit_price, :exit_timestamp,
        :result, :time_to_result,
        :direction, TRUE, :source,
        :decision_type, :decision_id,
        CAST(:features_snapshot AS JSONB),
        CAST(:config_snapshot AS JSONB),
        NOW(),
        :mae_at, :mfe_at,
        :barrier_touched, :barrier_touched_at, :intrabar_convention,
        :final_return_pct, :net_return_pct, :fee_roundtrip_pct_applied,
        :barrier_mode, :tp_pct_applied, :sl_pct_applied, :atr_pct_at_entry,
        :min_price_post_entry, :max_price_post_entry,
        :max_drawdown_pct, :max_profit_pct,
        :mae_pct, :mfe_pct,
        CAST(:exit_metrics_json AS JSONB)
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
      * decision_type 'ALLOW' para shadows aprovados; 'BLOCK' para L3_REJECTED
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

    # Task #306: ``trade_simulations.features_snapshot`` recebe o snapshot
    # COMPLETO de saída (gravado em ``shadow_trades.features_snapshot_exit``
    # pelo monitor). Isso garante que o dataset ML tenha as ~70 chaves
    # canônicas de indicadores no instante do desfecho — alinhado com o
    # bloco "Indicadores na SAÍDA" do modal. Fallback para o snapshot de
    # entrada quando o capture de saída devolveu:
    #   * NULL (trade fechado antes do deploy da Task #306), ou
    #   * marcador ``{"_capture_failed": True, ...}`` (provider sem dados
    #     no instante do fechamento — preserva pelo menos as features de
    #     entrada para o DatasetBuilder não perder a linha).
    # Contrato flat (Task #290) preservado: ambas as colunas-fonte são
    # ``{key: scalar}``; o marcador ``_capture_failed`` é descartado para
    # não contaminar o ``float()`` do ``DatasetBuilder``.
    exit_snap = shadow.features_snapshot_exit
    if isinstance(exit_snap, dict) and exit_snap and not exit_snap.get(
        "_capture_failed"
    ):
        features_for_sim = exit_snap
    else:
        features_for_sim = shadow.features_snapshot or {}

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
            # L3_REJECTED shadows usam decision_type='BLOCK' (satisfaz CHECK constraint).
            # Aprovados mantêm 'ALLOW'. O ML pode separar pelos dois valores.
            "decision_type": "BLOCK" if shadow.source == SHADOW_SOURCE_L3_REJECTED else "ALLOW",
            "decision_id": shadow.decision_id,
            "features_snapshot": json.dumps(features_for_sim, default=str),
            "config_snapshot": json.dumps(shadow.config_snapshot or {}, default=str),
            # Espelho das colunas de instrumentação (migration 071).
            "mae_at": shadow.mae_at,
            "mfe_at": shadow.mfe_at,
            "barrier_touched": shadow.barrier_touched,
            "barrier_touched_at": shadow.barrier_touched_at,
            "intrabar_convention": shadow.intrabar_convention,
            "final_return_pct": shadow.final_return_pct,
            "net_return_pct": shadow.net_return_pct,
            "fee_roundtrip_pct_applied": shadow.fee_roundtrip_pct_applied,
            "barrier_mode": shadow.barrier_mode,
            "tp_pct_applied": shadow.tp_pct_applied,
            "sl_pct_applied": shadow.sl_pct_applied,
            "atr_pct_at_entry": shadow.atr_pct_at_entry,
            # Espelho das colunas MAE/MFE (migration 062 drift — fixed in 072).
            "min_price_post_entry": shadow.min_price_post_entry,
            "max_price_post_entry": shadow.max_price_post_entry,
            "max_drawdown_pct": shadow.max_drawdown_pct,
            "max_profit_pct": shadow.max_profit_pct,
            "mae_pct": shadow.mae_pct,
            "mfe_pct": shadow.mfe_pct,
            "exit_metrics_json": json.dumps(shadow.exit_metrics_json, default=str)
            if shadow.exit_metrics_json is not None else None,
        },
    )
    row = res.fetchone()
    if row is None:
        logger.info(
            "[shadow] simulation already exists for decision_id=%s source=SHADOW "
            "(shadow_id=%s) — dedup hit",
            shadow.decision_id, shadow.id,
        )
    else:
        sim_id = row[0]
        logger.info(
            "[shadow] simulation recorded id=%s shadow_id=%s decision_id=%s "
            "result=%s symbol=%s",
            sim_id, shadow.id, shadow.decision_id, result, shadow.symbol,
        )

    # P0 fix — write pnl_pct / outcome / holding_seconds back to decisions_log.
    #
    # This is the root cause of the pnl_null_rate P0 issue: shadow trades were
    # completing (status=COMPLETED, pnl_pct set on the ShadowTrade row) but the
    # label was never propagated to decisions_log, so build_training_dataframe()
    # dropped every row (pnl_pct IS NULL → cannot label → silently skipped).
    #
    # Outcome vocabulary in decisions_log is lowercase tp/sl/timeout
    # (canonical post-14/05 regime — see feature_extractor.py build_training_dataframe).
    # WHERE pnl_pct IS NULL makes this idempotent on monitor retries.
    _outcome_dl_map = {"TP_HIT": "tp", "SL_HIT": "sl", "TIMEOUT": "timeout"}
    if shadow.decision_id is not None and shadow.pnl_pct is not None:
        dl_outcome = _outcome_dl_map.get(outcome)
        try:
            await db.execute(
                text("""
                    UPDATE decisions_log
                       SET pnl_pct         = :pnl_pct,
                           outcome         = :outcome,
                           holding_seconds = :holding_seconds
                     WHERE id = :decision_id
                       AND pnl_pct IS NULL
                """),
                {
                    "decision_id":     shadow.decision_id,
                    "pnl_pct":         shadow.pnl_pct,
                    "outcome":         dl_outcome,
                    "holding_seconds": shadow.holding_seconds,
                },
            )
            logger.info(
                "[shadow] decisions_log labelled decision_id=%s pnl_pct=%.4f "
                "outcome=%s holding_seconds=%s",
                shadow.decision_id, shadow.pnl_pct, dl_outcome, shadow.holding_seconds,
            )
        except Exception as exc:
            logger.warning(
                "[shadow] decisions_log label write failed for decision_id=%s: %s",
                shadow.decision_id, exc,
            )

    return row[0] if row is not None else None


async def backfill_decisions_log_pnl_from_shadows(limit: int = 500) -> int:
    """Back-fill pnl_pct / outcome / holding_seconds into decisions_log for
    rows that were created before the P0 writeback fix was deployed.

    Finds COMPLETED shadow trades whose linked decisions_log row still has
    pnl_pct IS NULL and applies the same outcome vocabulary mapping as
    record_as_simulation (TP_HIT→tp, SL_HIT→sl, TIMEOUT→timeout).

    Safe to call repeatedly — the UPDATE predicate (pnl_pct IS NULL) is
    idempotent. Processes up to ``limit`` rows per call so the monitor beat
    doesn't block on a large historical backlog.

    Returns the number of decisions_log rows updated.
    """
    from ..database import CeleryAsyncSessionLocal

    try:
        async with CeleryAsyncSessionLocal() as db:
            async with db.begin():
                result = await db.execute(
                    text("""
                        UPDATE decisions_log dl
                           SET pnl_pct         = st.pnl_pct,
                               outcome         = CASE st.outcome
                                                   WHEN 'TP_HIT'  THEN 'tp'
                                                   WHEN 'SL_HIT'  THEN 'sl'
                                                   WHEN 'TIMEOUT' THEN 'timeout'
                                                   ELSE lower(st.outcome)
                                                 END,
                               holding_seconds = st.holding_seconds
                          FROM shadow_trades st
                         WHERE st.decision_id  = dl.id
                           AND dl.pnl_pct      IS NULL
                           AND st.status       = 'COMPLETED'
                           AND st.pnl_pct      IS NOT NULL
                           AND dl.id IN (
                               SELECT dl2.id
                                 FROM decisions_log dl2
                                 JOIN shadow_trades st2 ON st2.decision_id = dl2.id
                                WHERE dl2.pnl_pct IS NULL
                                  AND st2.status  = 'COMPLETED'
                                  AND st2.pnl_pct IS NOT NULL
                                ORDER BY dl2.id
                                LIMIT :lim
                           )
                    """),
                    {"lim": limit},
                )
                updated = result.rowcount
        if updated:
            logger.info(
                "[shadow] backfill_decisions_log_pnl: labelled %d decisions_log row(s)",
                updated,
            )
        return updated
    except Exception:
        logger.exception("[shadow] backfill_decisions_log_pnl failed")
        return 0


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


# ── BLOCO B — espectro completo WATCHLIST_SPOT ────────────────────────────────


async def create_watchlist_spot_shadows(
    user_id,
    user_config: Dict[str, Any],
    pool_cfg: Dict[str, Any],
) -> int:
    """Cria shadow trades source='WATCHLIST_SPOT' para todos os símbolos da L1 spot.

    Objetivo: capturar outcomes de TODA a watchlist (não apenas aprovados L3),
    eliminando o viés de seleção que causava winrate ~76% no dataset de treino
    e impedia o ML de aprender a separar vencedores de perdedores.

    Gated por ``new_arch_capture_enabled`` em pool_cfg — quando False,
    retorna 0 sem tocar nada (comportamento IDÊNTICO ao atual).

    Regras:
    - Apenas SPOT (L1 ID via ``shadow_watchlist_l1_spot_id`` em pool_cfg)
    - Ponto de captura: L1 (pass-through) — antes de qualquer filtro de qualidade
    - Fidelidade: mesmo TP/SL/timeout que o trade real (de user_config)
    - Idempotente via ON CONFLICT (user_id, symbol) WHERE status='RUNNING'
    - Append-only: nunca apaga dados existentes
    - Captura da fase futures separada (WATCHLIST_FUT reservado — NÃO criar agora)
    """
    if not pool_cfg.get("new_arch_capture_enabled", False):
        # E.2 — flags=false → comportamento idêntico ao atual. Zero impacto.
        return 0

    l1_id = pool_cfg.get("shadow_watchlist_l1_spot_id")
    if not l1_id:
        logger.warning(
            "[shadow-ws] shadow_watchlist_l1_spot_id não configurado em pool_config — "
            "WATCHLIST_SPOT shadow não criado"
        )
        return 0
    # E.4 — ponto de captura imutável: a captura DEVE ser na L1 (pass-through),
    # ANTES de qualquer filtro de qualidade. O ID é lido de config — nunca hardcoded.
    # Se shadow_watchlist_l1_spot_id apontar para uma watchlist L2/L3, o espectro
    # completo fica comprometido e o viés de seleção retorna.
    # O operador é responsável por manter este ID apontando para a L1.

    from ..database import CeleryAsyncSessionLocal

    # ── Lê símbolos aprovados na L1 (pass-through, todos os que chegaram até aqui)
    watchlist_symbols: List[str] = []
    running_set: set = set()
    try:
        async with CeleryAsyncSessionLocal() as read_db:
            sym_rows = await read_db.execute(
                text("""
                    SELECT DISTINCT symbol
                    FROM pipeline_watchlist_assets
                    WHERE watchlist_id = :wid
                      AND (level_direction IS NULL OR level_direction = '' OR level_direction = 'up')
                    ORDER BY symbol
                """),
                {"wid": str(l1_id)},
            )
            watchlist_symbols = [r.symbol for r in sym_rows.fetchall()]

            if not watchlist_symbols:
                return 0

            # Símbolos com shadow RUNNING (qualquer source) — não duplicar
            run_rows = await read_db.execute(
                text("""
                    SELECT DISTINCT symbol FROM shadow_trades
                    WHERE user_id = :uid AND status IN ('RUNNING', 'PENDING')
                """),
                {"uid": str(user_id)},
            )
            running_set = {r.symbol for r in run_rows.fetchall()}
    except Exception:
        logger.exception(
            "[shadow-ws] query L1 symbols/running failed user=%s", user_id
        )
        return 0

    eligible = sorted(s for s in watchlist_symbols if s not in running_set)
    if not eligible:
        return 0

    # ── Features: lê indicadores via indicators_provider (mesmo merge path do pipeline)
    features_by_symbol: Dict[str, Dict[str, Any]] = {}
    try:
        from ..services.indicators_provider import get_merged_indicators
        async with CeleryAsyncSessionLocal() as ind_db:
            merged = await get_merged_indicators(ind_db, eligible)
            for sym, mi in merged.items():
                flat = mi.as_flat_dict()
                # Encapsula no formato que _build_features_snapshot espera:
                # {"indicators_snapshot": {key: {"value": v}}}
                features_by_symbol[sym] = {
                    "indicators_snapshot": {k: {"value": v} for k, v in flat.items()},
                    "source": "watchlist_spot_l1",
                }
    except Exception:
        logger.exception("[shadow-ws] indicators fetch failed user=%s", user_id)
        # Continua sem features (features_snapshot vazias) — shadow ainda é criado,
        # mas sem features ML. Melhor do que não criar.

    created = 0
    for symbol in eligible:
        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    # Monta _SyntheticDecision com features da L1
                    synthetic = _SyntheticDecision(
                        user_id=user_id,
                        symbol=symbol,
                        direction="SPOT",
                        strategy=None,
                        id=None,
                        created_at=datetime.now(timezone.utc),
                        metrics=features_by_symbol.get(symbol, {}),
                    )
                    new_id = await _create_from_decision(
                        own_db,
                        synthetic,
                        "WATCHLIST_SPOT_CAPTURE",
                        user_config,
                        source=SHADOW_SOURCE_WATCHLIST_SPOT,
                    )
                    if new_id is not None:
                        created += 1
                        logger.debug(
                            "[shadow-ws] WATCHLIST_SPOT created id=%s symbol=%s user=%s",
                            new_id, symbol, user_id,
                        )
        except Exception:
            logger.exception(
                "[shadow-ws] create failed symbol=%s user=%s", symbol, user_id
            )

    # E.3 — Carga monitorada: watchlist enxuta pós-filtro estrutural deve
    # ter ~80-120 símbolos. Se count > 200, o filtro estrutural pode não
    # estar funcionando (pool bruto vindo sem filtro).
    _watchlist_size = len(watchlist_symbols)
    _warn_threshold = 200
    if _watchlist_size > _warn_threshold:
        logger.warning(
            "[shadow-ws] WATCHLIST_SPOT_LOAD_WARNING|user=%s|watchlist_size=%d "
            "(expected ~80-120 pós-filtro estrutural — verificar pool_structural_filter)",
            user_id, _watchlist_size,
        )

    logger.info(
        "[shadow-ws] WATCHLIST_SPOT|user=%s|watchlist=%d|running_skip=%d|eligible=%d|created=%d",
        user_id,
        _watchlist_size,
        len(watchlist_symbols) - len(eligible),
        len(eligible),
        created,
    )
    return created


# ── Strategy Lab: direct profile-attributed shadows ──────────────────────────
#
# These functions bypass decisions_log deduplication entirely.
# Multiple L3 profiles can capture the same symbol in parallel.
# source='L3' is canonical so the shadow monitor tracks them like real L3 shadows.
# Idempotency: ON CONFLICT ON CONSTRAINT uq_shadow_lab_profile_symbol_bucket DO NOTHING
# means two calls for the same (profile_id, symbol, source, hour) are safe.

_INSERT_STRATEGY_LAB_SQL = text("""
    INSERT INTO shadow_trades (
        id,
        decision_id, user_id, symbol, strategy, direction,
        amount_usdt, entry_price, entry_timestamp,
        tp_price, sl_price, tp_pct, sl_pct, timeout_candles,
        status, skip_reason, source, config_snapshot, features_snapshot,
        last_processed_time,
        ttt_enabled, ttt_tp_pct, ttt_timeout_minutes,
        barrier_mode, tp_pct_applied, sl_pct_applied,
        profile_id, profile_version, profile_name, strategy_type, rules_snapshot
    ) VALUES (
        gen_random_uuid(),
        NULL, :user_id, :symbol, :strategy, :direction,
        :amount_usdt, :entry_price, :entry_timestamp,
        :tp_price, :sl_price, :tp_pct, :sl_pct, :timeout_candles,
        :status, NULL, :source,
        CAST(:config_snapshot AS JSONB),
        CAST(:features_snapshot AS JSONB),
        :last_processed_time,
        :ttt_enabled, :ttt_tp_pct, :ttt_timeout_minutes,
        :barrier_mode, :tp_pct_applied, :sl_pct_applied,
        CAST(:profile_id AS UUID), :profile_version, :profile_name,
        :strategy_type, CAST(:rules_snapshot AS JSONB)
    )
    ON CONFLICT (profile_id, symbol, source, shadow_lab_hour_bucket(created_at)) DO NOTHING
    RETURNING id
""")


async def create_strategy_lab_shadows(
    user_id,
    profile_id,
    profile_version: Optional[datetime],
    profile_name: str,
    strategy_type: str,
    rules_snapshot: Optional[Dict[str, Any]],
    allow_decisions: List[Dict[str, Any]],
    assets_by_symbol: Dict[str, Dict[str, Any]],
    execution_id: str,
    promotion_at: datetime,
    db: Any,
) -> int:
    """Strategy Lab: create profile-attributed ALLOW shadows bypassing decisions_log dedup.

    Key design points:
    - decision_id = NULL always (bypasses _persist_decision_logs deduplication)
    - source = 'L3' (shadow monitor tracks them like real L3 shadows)
    - profile_id set (distinguishes Strategy Lab from canonical L3)
    - ON CONFLICT ON CONSTRAINT uq_shadow_lab_profile_symbol_bucket DO NOTHING
    - Fire-and-forget pattern: logs errors, never raises

    Returns count of new shadow rows inserted.
    """
    import json as _json

    if not allow_decisions:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    # Load tp_pct / sl_pct from spot engine config
    tp_pct = 3.0
    sl_pct = 2.0
    timeout_candles = SHADOW_TIMEOUT_CANDLES
    ttt_enabled = TTT_ENABLED_DEFAULT
    ttt_tp_pct = TTT_TP_PCT_DEFAULT
    ttt_timeout_minutes = TTT_TIMEOUT_MINUTES_DEFAULT

    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            se_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            se_row = se_res.scalar_one_or_none()
            if se_row:
                try:
                    _se = SpotEngineConfig.from_config_json(se_row.config_json)
                    tp_pct = _SHADOW_TP_PCT_OVERRIDE or float(_se.selling.take_profit_pct)
                    sl_pct = _SHADOW_SL_PCT_OVERRIDE or float(
                        _se.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    )
                except Exception:
                    pass
    except Exception:
        logger.debug("[StrategyLab-allow] config load failed user=%s", user_id)

    created = 0
    profile_id_str = str(profile_id) if profile_id is not None else None
    rules_json = _json.dumps(rules_snapshot, default=str) if rules_snapshot else None

    for d in allow_decisions:
        symbol = d.get("symbol")
        if not symbol:
            continue

        # Get current market price from asset dict
        _asset = assets_by_symbol.get(symbol) or d.get("_asset") or {}
        entry_price = None
        if isinstance(_asset, dict):
            _p = _asset.get("current_price") or _asset.get("price")
            if _p is not None:
                try:
                    entry_price = float(_p)
                except (TypeError, ValueError):
                    pass

        if entry_price and entry_price > 0 and tp_pct > 0 and sl_pct > 0:
            tp_price = entry_price * (1 + tp_pct / 100.0)
            sl_price = entry_price * (1 - sl_pct / 100.0)
            initial_status = "RUNNING"
        else:
            tp_price = None
            sl_price = None
            initial_status = "PENDING"

        # Build features snapshot from decision metrics
        _metrics = d.get("metrics") or {}
        _snap = _metrics.get("indicators_snapshot") or {}
        features_flat: Dict[str, Any] = {}
        if isinstance(_snap, dict):
            for key, entry in _snap.items():
                if isinstance(entry, dict) and "value" in entry:
                    features_flat[key] = entry.get("value")
                else:
                    features_flat[key] = entry

        config_snap = {
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "timeout_candles": timeout_candles,
            "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
            "ttt_enabled": ttt_enabled,
            "ttt_tp_pct": ttt_tp_pct,
            "ttt_timeout_minutes": ttt_timeout_minutes,
            "profile_id": profile_id_str,
            "profile_name": profile_name,
            "execution_id": execution_id,
        }

        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    res = await own_db.execute(
                        _INSERT_STRATEGY_LAB_SQL,
                        {
                            "user_id": user_id,
                            "symbol": symbol,
                            "strategy": d.get("strategy"),
                            "direction": d.get("direction", "SPOT"),
                            "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
                            "entry_price": entry_price,
                            "entry_timestamp": promotion_at,
                            "tp_price": tp_price,
                            "sl_price": sl_price,
                            "tp_pct": tp_pct or None,
                            "sl_pct": sl_pct or None,
                            "timeout_candles": timeout_candles,
                            "status": initial_status,
                            "source": SHADOW_SOURCE_L3,
                            "config_snapshot": _json.dumps(config_snap, default=str),
                            "features_snapshot": _json.dumps(features_flat, default=str),
                            "last_processed_time": promotion_at,
                            "ttt_enabled": ttt_enabled,
                            "ttt_tp_pct": ttt_tp_pct,
                            "ttt_timeout_minutes": ttt_timeout_minutes,
                            "barrier_mode": "FIXED",
                            "tp_pct_applied": tp_pct or None,
                            "sl_pct_applied": sl_pct or None,
                            "profile_id": profile_id_str,
                            "profile_version": profile_version,
                            "profile_name": profile_name,
                            "strategy_type": strategy_type,
                            "rules_snapshot": rules_json,
                        },
                    )
                    row = res.fetchone()
                    if row is not None:
                        created += 1
                        logger.debug(
                            "[StrategyLab-allow] created id=%s symbol=%s profile=%s",
                            row[0], symbol, profile_name,
                        )
        except Exception:
            logger.exception(
                "[StrategyLab-allow] create failed symbol=%s profile=%s",
                symbol, profile_name,
            )

    if created:
        logger.info(
            "[StrategyLab-allow] profile=%s created=%d allow_decisions=%d user=%s",
            profile_name, created, len(allow_decisions), user_id,
        )
    return created


async def create_strategy_lab_rejected_shadows(
    user_id,
    profile_id,
    profile_version: Optional[datetime],
    profile_name: str,
    strategy_type: str,
    rules_snapshot: Optional[Dict[str, Any]],
    block_decisions: List[Dict[str, Any]],
    assets_by_symbol: Dict[str, Dict[str, Any]],
    execution_id: str,
    promotion_at: datetime,
    db: Any,
) -> int:
    """Strategy Lab: create profile-attributed BLOCK shadows for counterfactual analysis.

    Same design as create_strategy_lab_shadows but for BLOCK decisions.
    source='L3' — same monitor tracks them.
    Uses uq_shadow_lab_profile_symbol_bucket for idempotency.
    Fire-and-forget: logs errors, never raises.
    """
    import json as _json

    if not block_decisions:
        return 0

    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig

    tp_pct = 3.0
    sl_pct = 2.0
    timeout_candles = SHADOW_TIMEOUT_CANDLES
    ttt_enabled = TTT_ENABLED_DEFAULT
    ttt_tp_pct = TTT_TP_PCT_DEFAULT
    ttt_timeout_minutes = TTT_TIMEOUT_MINUTES_DEFAULT

    try:
        async with CeleryAsyncSessionLocal() as cfg_db:
            se_res = await cfg_db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == user_id,
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                ).limit(1)
            )
            se_row = se_res.scalar_one_or_none()
            if se_row:
                try:
                    _se = SpotEngineConfig.from_config_json(se_row.config_json)
                    tp_pct = _SHADOW_TP_PCT_OVERRIDE or float(_se.selling.take_profit_pct)
                    sl_pct = _SHADOW_SL_PCT_OVERRIDE or float(
                        _se.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    )
                except Exception:
                    pass
    except Exception:
        logger.debug("[StrategyLab-block] config load failed user=%s", user_id)

    created = 0
    profile_id_str = str(profile_id) if profile_id is not None else None
    rules_json = _json.dumps(rules_snapshot, default=str) if rules_snapshot else None

    for d in block_decisions:
        symbol = d.get("symbol")
        if not symbol:
            continue

        _asset = assets_by_symbol.get(symbol) or d.get("_asset") or {}
        entry_price = None
        if isinstance(_asset, dict):
            _p = _asset.get("current_price") or _asset.get("price")
            if _p is not None:
                try:
                    entry_price = float(_p)
                except (TypeError, ValueError):
                    pass

        if entry_price and entry_price > 0 and tp_pct > 0 and sl_pct > 0:
            tp_price = entry_price * (1 + tp_pct / 100.0)
            sl_price = entry_price * (1 - sl_pct / 100.0)
            initial_status = "RUNNING"
        else:
            tp_price = None
            sl_price = None
            initial_status = "PENDING"

        _metrics = d.get("metrics") or {}
        _snap = _metrics.get("indicators_snapshot") or {}
        features_flat: Dict[str, Any] = {}
        if isinstance(_snap, dict):
            for key, entry in _snap.items():
                if isinstance(entry, dict) and "value" in entry:
                    features_flat[key] = entry.get("value")
                else:
                    features_flat[key] = entry

        config_snap = {
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "timeout_candles": timeout_candles,
            "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
            "ttt_enabled": ttt_enabled,
            "ttt_tp_pct": ttt_tp_pct,
            "ttt_timeout_minutes": ttt_timeout_minutes,
            "profile_id": profile_id_str,
            "profile_name": profile_name,
            "execution_id": execution_id,
            "l3_decision": "BLOCK",
            "l3_score": d.get("score"),
        }

        try:
            async with CeleryAsyncSessionLocal() as own_db:
                async with own_db.begin():
                    res = await own_db.execute(
                        _INSERT_STRATEGY_LAB_SQL,
                        {
                            "user_id": user_id,
                            "symbol": symbol,
                            "strategy": d.get("strategy"),
                            "direction": d.get("direction", "SPOT"),
                            "amount_usdt": SHADOW_TRADE_AMOUNT_USDT,
                            "entry_price": entry_price,
                            "entry_timestamp": promotion_at,
                            "tp_price": tp_price,
                            "sl_price": sl_price,
                            "tp_pct": tp_pct or None,
                            "sl_pct": sl_pct or None,
                            "timeout_candles": timeout_candles,
                            "status": initial_status,
                            "source": SHADOW_SOURCE_L3,
                            "config_snapshot": _json.dumps(config_snap, default=str),
                            "features_snapshot": _json.dumps(features_flat, default=str),
                            "last_processed_time": promotion_at,
                            "ttt_enabled": ttt_enabled,
                            "ttt_tp_pct": ttt_tp_pct,
                            "ttt_timeout_minutes": ttt_timeout_minutes,
                            "barrier_mode": "FIXED",
                            "tp_pct_applied": tp_pct or None,
                            "sl_pct_applied": sl_pct or None,
                            "profile_id": profile_id_str,
                            "profile_version": profile_version,
                            "profile_name": profile_name,
                            "strategy_type": strategy_type,
                            "rules_snapshot": rules_json,
                        },
                    )
                    row = res.fetchone()
                    if row is not None:
                        created += 1
                        logger.debug(
                            "[StrategyLab-block] created id=%s symbol=%s profile=%s",
                            row[0], symbol, profile_name,
                        )
        except Exception:
            logger.exception(
                "[StrategyLab-block] create failed symbol=%s profile=%s",
                symbol, profile_name,
            )

    if created:
        logger.info(
            "[StrategyLab-block] profile=%s created=%d block_decisions=%d user=%s",
            profile_name, created, len(block_decisions), user_id,
        )
    return created
