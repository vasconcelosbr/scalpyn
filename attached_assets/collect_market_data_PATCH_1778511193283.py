# ============================================================
# PATCH: collect_market_data.py — remover OHLCV 1h e chain do collect_all
#
# Este é o patch mais invasivo. Mexe no corpo de _collect_all_async()
# e no wrapper collect_all(). Aplicar com atenção linha a linha.
# ============================================================


# ──────────────────────────────────────────────────────────────
# PATCH 1 — remover loop OHLCV de _collect_all_async()
# LOCALIZAÇÃO: função _collect_all_async, linha ~218 até ~609
#
# AÇÃO: remover integralmente o bloco:
#
#     async def _inner(db, queue_mode: bool = False) -> int:
#         ...
#         for symbol in symbols:   # ← este for inteiro
#             try:
#                 df = await market_data_service.fetch_ohlcv(symbol, "1h", ...)
#                 ...
#             except Exception as e:
#                 ...
#
#         # Also fetch tickers for metadata ...  ← manter este bloco
#
# O que FICA em _collect_all_async() após o patch:
#   • Carregamento de raw_symbols / valid_symbols / symbols (linhas 152-216)
#   • _inner(db) com APENAS o bloco de tickers (fetch_all_tickers → bulk upsert)
#   • Dispatch para persistence queue ou run_db_task
#
# O que SAI:
#   • Variáveis: collected, failures, _cycle_t0 (usadas só pelo loop OHLCV)
#   • Loop for symbol in symbols com fetch_ohlcv, persist, probe, market_metadata por símbolo
#   • Log [OHLCV-COMMIT] flow=1h (específico do path OHLCV)
#   • import ohlcv_metrics (se não usado em outro lugar de _inner)
#   • if collected == 0: raise RuntimeError (era guard do OHLCV)
#
# NOVA versão simplificada de _inner (substituir o bloco existente):

NOVO_INNER_TEMPLATE = '''
    async def _inner(db, queue_mode: bool = False) -> int:
        """Ticker bulk fetch + market_metadata UPSERT only.

        OHLCV 1h foi migrado para collect_structural_30m (refactor structural-30m).
        Este path é responsável exclusivamente por:
            • fetch_all_tickers() — 1 chamada bulk Gate.io
            • bulk UPSERT em market_metadata (price, change, volume, spread)
        Retorna o número de tickers upsertados com sucesso.
        """
        import time as _time
        _cycle_t0 = _time.monotonic()

        # ── Ticker bulk + metadata ────────────────────────────────────────────
        try:
            tickers = await market_data_service.fetch_all_tickers()
            if not tickers:
                logger.warning("fetch_all_tickers returned empty — retrying once after 3 s…")
                await asyncio.sleep(3)
                tickers = await market_data_service.fetch_all_tickers()

            now_ts = datetime.now(timezone.utc)
            valid_rows: list[dict] = []
            for ticker in tickers:
                try:
                    pair = ticker.get("currency_pair", "")
                    if not pair.endswith("_USDT"):
                        continue
                    price = float(ticker.get("last", 0) or 0)
                    if price <= 0:
                        continue
                    change = float(ticker.get("change_percentage", 0) or 0)
                    volume = float(ticker.get("quote_volume", 0) or 0)
                    spread = market_data_service.compute_spread_from_ticker(ticker)
                    valid_rows.append({
                        "symbol": pair,
                        "price":  price,
                        "change": change,
                        "volume": volume,
                        "spread": spread,
                        "updated": now_ts,
                    })
                except Exception as te:
                    logger.debug(
                        "[COLLECT-TICKERS] validation failed for %s: %s",
                        ticker.get("currency_pair", "?"), te,
                    )
                    continue

            valid_rows.sort(key=lambda r: r["symbol"])

            if queue_mode:
                for r in valid_rows:
                    await _pq.enqueue_or_log(
                        producer="collect-1h-tickers",
                        msg=_pq.MarketMetadataUpsert(
                            category="ingest",
                            enqueued_at=_pq.now_monotonic(),
                            symbol=r["symbol"],
                            last_updated=r["updated"],
                            price=r["price"],
                            price_change_24h=r["change"],
                            volume_24h=r["volume"],
                            spread_pct=r["spread"],
                        ),
                    )
                ticker_ok = len(valid_rows)
            else:
                ticker_ok = await _bulk_upsert_market_metadata(
                    db, valid_rows, origin="ticker-60s",
                )

            _cycle_dt = _time.monotonic() - _cycle_t0
            logger.info(
                "[COLLECT-TICKERS] upserted=%d fetched=%d duration_s=%.2f",
                ticker_ok, len(tickers), _cycle_dt,
            )
            return ticker_ok

        except Exception as exc:
            logger.error("[COLLECT-TICKERS] failed: %s", exc, exc_info=True)
            return 0
'''

# ──────────────────────────────────────────────────────────────
# PATCH 2 — remover chain compute do wrapper collect_all()
# LOCALIZAÇÃO: collect_all() wrapper, linhas ~674-685
#
# ANTES:
#     if count > 0:
#         from . import task_dispatch
#         task_dispatch.enqueue(
#             "app.tasks.compute_indicators.compute",
#             dedup_key="compute",
#             ttl_seconds=660,
#         )
#     return f"Collected {count} symbols"
#
# DEPOIS:
#     # Chain para compute_indicators.compute (1h) removido no refactor
#     # structural-30m. O pipeline estrutural agora é disparado por
#     # collect_structural_30m → compute_30m → score → evaluate.
#     return f"[COLLECT-TICKERS] Updated metadata for {count} tickers"
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# PATCH 3 — remover imports órfãos de _collect_all_async
# LOCALIZAÇÃO: cabeçalho de _collect_all_async, linhas ~143-149
#
# Remover imports que eram usados apenas pelo loop OHLCV e não pelo
# bloco de tickers. Verificar se ainda são usados em outro lugar do
# arquivo antes de remover.
#
# Candidatos a remover de _collect_all_async (verificar uso):
#   from sqlalchemy import text       ← usado pelo ticker? SIM (manter)
#   _REQUIRED_OHLCV_COLUMNS           ← usado só pelo loop OHLCV (remover local)
#   ohlcv_metrics import              ← usado só pelo loop OHLCV (remover de _inner)
#
# Módulo-level (_REQUIRED_OHLCV_COLUMNS = [...] no topo do arquivo):
#   Manter por enquanto — collect_5m ainda usa a constante.
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# PATCH 4 — docker-compose.yml (correção obrigatória para dev local)
# LOCALIZAÇÃO: docker-compose.yml, serviço celery_worker
#
# ANTES:
#     command: celery -A app.tasks.celery_app worker
#              --loglevel=info --concurrency=2 -Q celery
#
# DEPOIS:
#     command: celery -A app.tasks.celery_app worker
#              --loglevel=info --concurrency=4
#              -Q microstructure,structural,execution
#
# MOTIVO: fila "celery" não existe no TASK_ROUTES — workers locais
# nunca processavam nenhuma task. -Q correto = as 3 filas declaradas.
# ──────────────────────────────────────────────────────────────
