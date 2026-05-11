# ============================================================
# PATCH: compute_indicators.py — adicionar compute_30m
#
# Aplicar manualmente. Cada bloco é identificado por:
#   LOCALIZAÇÃO: arquivo + número de linha aproximado de referência
#   AÇÃO: o que fazer
#   ANTES / DEPOIS: diff exato
# ============================================================


# ──────────────────────────────────────────────────────────────
# PATCH 1 — _derive_min_candles
# LOCALIZAÇÃO: função _derive_min_candles, linha ~225
# AÇÃO: adicionar "30m" ao ramo que exige 48 candles
#
# ANTES:
#     48 if timeframe == "5m" else 24,
#
# DEPOIS:
#     48 if timeframe in ("5m", "30m") else 24,
#
# MOTIVO: 30m estrutural precisa de ao menos 24h de histórico
# (48 candles × 30m = 24h). O ramo anterior "else 24" daria
# apenas 12h para 30m, insuficiente para EMA200 e ADX(14).
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# PATCH 2 — nova função _compute_30m_async
# LOCALIZAÇÃO: após _compute_5m_async (antes do wrapper compute_5m)
# AÇÃO: inserir função nova — copiar bloco abaixo integralmente
# ──────────────────────────────────────────────────────────────

# ---------- BLOCO A INSERIR (compute_indicators.py) ----------

async def _compute_30m_async():
    """Compute structural indicators on 30m OHLCV candles.

    Mirrors _compute_async (1h path) with the following differences:
        • reads ``ohlcv`` WHERE timeframe = '30m'
        • writes ``indicators`` with timeframe = '30m', scheduler_group = 'structural_30m'
        • _derive_min_candles receives '30m' → requires 48 candles (24h history)
        • chains to compute_scores.score via the same dedup_key="score"

    The dedup_key="score" is intentionally shared with the (now-retired)
    compute-1h path. Since Opção A replaces 1h with 30m entirely, there
    is no concurrent compute-1h trigger anymore — the dedup is a no-op
    safety net, not an active conflict resolver.
    """
    # Imports mirror _compute_async exactly — same dependencies.
    from ..database import run_db_task
    from ..services.config_service import config_service
    from sqlalchemy import text
    import pandas as pd

    _TIMEFRAME = "30m"
    _SCHEDULER_GROUP = "structural_30m"

    logger.info("[COMPUTE-30m] Starting 30m indicator computation…")

    async def _inner(db):
        # ── Load active symbols with fresh 30m candles ────────────────────
        symbols_rows = (await db.execute(text("""
            SELECT DISTINCT o.symbol
            FROM ohlcv o
            JOIN pool_coins p ON o.symbol = p.symbol
            WHERE p.is_active = true
              AND p.market_type = 'spot'
              AND o.timeframe = :tf
              AND o.time > now() - interval '7 days'
            ORDER BY o.symbol
        """), {"tf": _TIMEFRAME})).fetchall()

        symbols = [r.symbol for r in symbols_rows]
        logger.info("[COMPUTE-30m] symbols_to_process=%d", len(symbols))

        if not symbols:
            logger.warning("[COMPUTE-30m] no symbols with fresh 30m candles — skipping")
            return 0

        computed = 0
        skipped = 0

        for symbol in symbols:
            try:
                # ── Load config ───────────────────────────────────────────
                # config_service.get_config is the only sanctioned read path
                # (invariant #1 from celery_app.py docstring).
                try:
                    indicators_config = await config_service.get_config(
                        user_id=None, pool_id=None, config_type="indicators"
                    )
                except Exception as cfg_exc:
                    logger.warning(
                        "[COMPUTE-30m] config load failed for %s — skipping: %s",
                        symbol, cfg_exc,
                    )
                    skipped += 1
                    continue

                min_candles = _derive_min_candles(indicators_config, _TIMEFRAME)

                # ── Fetch candles ─────────────────────────────────────────
                rows = (await db.execute(text("""
                    SELECT time, open, high, low, close, volume, quote_volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                    ORDER BY time DESC
                    LIMIT :limit
                """), {"symbol": symbol, "tf": _TIMEFRAME, "limit": min_candles + 10}
                )).fetchall()

                if len(rows) < min_candles:
                    logger.debug(
                        "[COMPUTE-30m] Skipping %s: only %d candles (need ≥%d)",
                        symbol, len(rows), min_candles,
                    )
                    skipped += 1
                    continue

                # Build DataFrame — same shape as _compute_async
                df = pd.DataFrame(rows, columns=[
                    "time", "open", "high", "low", "close", "volume", "quote_volume"
                ])
                df = df.sort_values("time").reset_index(drop=True)
                for col in ("open", "high", "low", "close", "volume", "quote_volume"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")

                # ── Compute indicators ────────────────────────────────────
                # Reuse the existing _compute_indicators_for_df helper so
                # all indicator logic (RSI, MACD, EMA, ADX, BB, DI+) stays
                # in one place. Pass timeframe so period guards are correct.
                indicators_json = _compute_indicators_for_df(
                    df, indicators_config, timeframe=_TIMEFRAME
                )

                if not indicators_json:
                    logger.debug(
                        "[COMPUTE-30m] No indicators computed for %s — skipping persist",
                        symbol,
                    )
                    skipped += 1
                    continue

                now_ts = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )

                # ── Persist indicators ────────────────────────────────────
                try:
                    async with db.begin_nested():
                        await db.execute(text("""
                            INSERT INTO indicators
                                (time, symbol, timeframe, market_type,
                                 scheduler_group, indicators_json)
                            VALUES
                                (:time, :symbol, :timeframe, :market_type,
                                 :scheduler_group, :indicators_json::jsonb)
                            ON CONFLICT (time, symbol, timeframe)
                            DO UPDATE SET
                                indicators_json  = EXCLUDED.indicators_json,
                                scheduler_group  = EXCLUDED.scheduler_group
                        """), {
                            "time":            now_ts,
                            "symbol":          symbol,
                            "timeframe":       _TIMEFRAME,
                            "market_type":     "spot",
                            "scheduler_group": _SCHEDULER_GROUP,
                            "indicators_json": __import__("json").dumps(indicators_json),
                        })
                except Exception as sp_exc:
                    logger.error(
                        "[COMPUTE-30m] SAVEPOINT failed for %s — rolling back symbol: %s",
                        symbol, sp_exc,
                    )
                    if not db.is_active:
                        break
                    skipped += 1
                    continue

                logger.debug("[COMPUTE-30m][OK] symbol=%s", symbol)
                computed += 1

            except Exception as exc:
                logger.error(
                    "[COMPUTE-30m][FAILED] symbol=%s error=%s",
                    symbol, exc, exc_info=True,
                )
                skipped += 1
                if not db.is_active:
                    break
                continue

        logger.info(
            "[COMPUTE-30m] done computed=%d skipped=%d total=%d",
            computed, skipped, len(symbols),
        )
        return computed

    return await run_db_task(_inner, celery=True)

# ---------- FIM DO BLOCO A INSERIR ----------


# ──────────────────────────────────────────────────────────────
# PATCH 3 — wrapper Celery compute_30m
# LOCALIZAÇÃO: após o bloco acima (antes de compute_5m ou EOF)
# AÇÃO: inserir wrapper Celery abaixo
# ──────────────────────────────────────────────────────────────

# ---------- BLOCO B INSERIR (compute_indicators.py) ----------

# @celery_app.task(name="app.tasks.compute_indicators.compute_30m")
# def compute_30m():
#     """Celery entry point — compute structural indicators on 30m candles.
#
#     Enqueued by: collect_structural_30m.run (via task_dispatch.enqueue)
#     Chains to:   compute_scores.score (dedup_key='score', ttl=660s)
#
#     The dedup_key='score' is shared with the retired compute-1h path.
#     Since Opção A removes compute-1h entirely, there is no concurrent
#     trigger — the dedup is a safety net only.
#     """
#     count = _run_async(_compute_30m_async())
#     from . import task_dispatch
#     task_dispatch.enqueue(
#         "app.tasks.compute_scores.score",
#         dedup_key="score",
#         ttl_seconds=660,
#     )
#     return f"[COMPUTE-30m] Computed indicators for {count} symbols"

# NOTE: remova os comentários (#) acima ao inserir no arquivo real.
# Estão comentados aqui apenas para evitar execução acidental deste patch.

# ---------- FIM DO BLOCO B INSERIR ----------


# ──────────────────────────────────────────────────────────────
# PATCH 4 — remover chain compute→score do wrapper compute()
# LOCALIZAÇÃO: função compute() existente, ~linha 429-439
# AÇÃO: remover o task_dispatch.enqueue("compute_scores.score") de compute()
#       O compute() (1h) será aposentado junto com collect_all OHLCV.
#       Se quiser manter compute() por segurança temporária, remova apenas
#       o enqueue de score — assim ele computa mas não dispara o pipeline.
#
# ANTES:
#     @celery_app.task(name="app.tasks.compute_indicators.compute")
#     def compute():
#         count = _run_async(_compute_async())
#         from . import task_dispatch
#         task_dispatch.enqueue(
#             "app.tasks.compute_scores.score",
#             dedup_key="score",
#             ttl_seconds=660,
#         )
#         return f"Computed indicators for {count} symbols"
#
# DEPOIS (Opção A — substituição total):
#     # compute() (1h path) aposentado pelo refactor structural-30m.
#     # Mantido como stub para não quebrar imports históricos.
#     # Remover na próxima limpeza de código quando collect_all OHLCV
#     # for removido do beat_schedule e confirmado estável em prod.
#     @celery_app.task(name="app.tasks.compute_indicators.compute")
#     def compute():
#         logger.warning(
#             "[COMPUTE-1h] DEPRECATED — este path foi substituído pelo "
#             "compute_30m no refactor structural-30m. Task não faz nada."
#         )
#         return "DEPRECATED — use compute_30m"
# ──────────────────────────────────────────────────────────────
