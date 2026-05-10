-- Task #256 — Re-medição pós-fix. Rodar no Cloud SQL com --database=scalpyn.
-- Comparar saídas com attached_assets/studio_results_20260510_*.json.
--
-- Critérios de sucesso (ver .local/tasks/task-256.md "Done looks like"):
--   * decisions/h > 0 em ≥80% das últimas 17h
--   * B1 vazio (nenhuma TX com xact_age > 2min)
--   * B2 vazio (nenhum lock waiter)
--   * D1: COUNT(is_tradable) < COUNT(is_active)  (após qualquer ajuste manual)
--   * E1: trades > 0 nos últimos 7d (ou justificativa registrada)

-- ─────────────────────────────────────────────────────────────────────────
-- A1 — Throughput de decisões por hora (últimas 17h)
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  date_trunc('hour', created_at) AS hour_utc,
  COUNT(*)                       AS decisions,
  COUNT(*) FILTER (WHERE decision = 'ALLOW') AS allow,
  COUNT(*) FILTER (WHERE decision = 'BLOCK') AS block,
  COUNT(*) FILTER (WHERE outcome IS NOT NULL) AS outcome_filled
FROM decisions_log
WHERE created_at > NOW() - INTERVAL '17 hours'
GROUP BY 1
ORDER BY 1 DESC;

-- A2 — Distribuição de scores nas últimas 24h
SELECT
  CASE
    WHEN score IS NULL THEN 'n/a'
    WHEN score < 20 THEN '0-20'
    WHEN score < 40 THEN '20-40'
    WHEN score < 60 THEN '40-60'
    WHEN score < 80 THEN '60-80'
    ELSE '80-100'
  END AS bucket,
  COUNT(*) AS n
FROM decisions_log
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY 1;

-- ─────────────────────────────────────────────────────────────────────────
-- B1 — TXs órfãs (filtro corrigido — cobre state='active' com SAVEPOINT)
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  pid,
  state,
  application_name,
  NOW() - xact_start AS xact_age,
  NOW() - state_change AS state_age,
  LEFT(query, 120) AS query_preview
FROM pg_stat_activity
WHERE datname = current_database()
  AND xact_start IS NOT NULL
  AND NOW() - xact_start > INTERVAL '2 minutes'
  AND state IN ('active','idle in transaction','idle in transaction (aborted)')
ORDER BY xact_start ASC;

-- B2 — Quem está bloqueando quem
SELECT
  blocked.pid     AS blocked_pid,
  blocked.query   AS blocked_query,
  blocking.pid    AS blocking_pid,
  blocking.query  AS blocking_query,
  pg_blocking_pids(blocked.pid) AS blocking_chain
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking
  ON blocking.pid = ANY (pg_blocking_pids(blocked.pid))
WHERE blocked.datname = current_database();

-- B3 — Atividade total + dead tuples nas hot tables
SELECT
  relname,
  n_live_tup,
  n_dead_tup,
  ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct,
  last_autovacuum,
  last_autoanalyze
FROM pg_stat_user_tables
WHERE relname IN ('market_metadata','ohlcv','decisions_log','pool_coins','trade_tracking')
ORDER BY n_dead_tup DESC;

-- B4 — Watchdog: scan + kills nas últimas 24h (Prometheus, ler via /metrics)
-- (Sem SQL — checar via curl em /metrics: scalpyn_orphan_tx_killed_total)

-- ─────────────────────────────────────────────────────────────────────────
-- C1 — Deadlocks lifetime (deve estar estável após Task #251)
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  datname,
  deadlocks,
  xact_commit,
  xact_rollback,
  ROUND(100.0 * xact_rollback / NULLIF(xact_commit + xact_rollback, 0), 4) AS rollback_pct
FROM pg_stat_database
WHERE datname = current_database();

-- ─────────────────────────────────────────────────────────────────────────
-- D1 — Gate ingestion vs execution (deve ser is_tradable < is_active)
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  COUNT(*) FILTER (WHERE is_active)   AS active_count,
  COUNT(*) FILTER (WHERE is_tradable) AS tradable_count,
  COUNT(*) FILTER (WHERE is_active AND is_tradable) AS active_and_tradable,
  COUNT(*) FILTER (WHERE is_active AND NOT is_tradable) AS active_not_tradable,
  ROUND(100.0 * COUNT(*) FILTER (WHERE is_tradable)
              / NULLIF(COUNT(*) FILTER (WHERE is_active), 0), 2) AS tradable_pct_of_active
FROM pool_coins;

-- D3 — Símbolos tradable que NUNCA foram tocados pelo operador via API
-- (proxy: discovered_at IS NOT NULL E origin='discovered' indica que
-- veio de auto_discover; foram backfilled como tradable=true pela
-- migração 043). Operador decide se quer mass-disable.
SELECT
  origin,
  COUNT(*)                     AS total,
  COUNT(*) FILTER (WHERE is_tradable) AS tradable
FROM pool_coins
WHERE is_active
GROUP BY 1
ORDER BY 1;

-- ─────────────────────────────────────────────────────────────────────────
-- E1 — Trades reais nos últimos 7d
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  COUNT(*)                                                    AS total_trades,
  COUNT(*) FILTER (WHERE outcome = 'tp')                      AS tp,
  COUNT(*) FILTER (WHERE outcome = 'sl')                      AS sl,
  COUNT(*) FILTER (WHERE outcome = 'timeout')                 AS timeout,
  COUNT(*) FILTER (WHERE outcome IS NULL AND status = 'open') AS still_open,
  AVG(pnl_pct)                                                AS avg_pnl_pct,
  MAX(exit_time)                                              AS last_exit
FROM trade_tracking
WHERE is_simulated = false
  AND COALESCE(exit_time, entry_time) > NOW() - INTERVAL '7 days';
