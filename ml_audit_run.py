"""
ML Pipeline Coverage Audit — local execution via public IP.
Extracts real numbers from DB and code. No hypotheses.
"""
import json, subprocess, sys
import psycopg2
import psycopg2.extras
from datetime import datetime

# ── Get DB credentials from Secret Manager ──────────────────────────────────
result = subprocess.run(
    ["gcloud", "secrets", "versions", "access", "latest",
     "--secret=database-url", "--project=clickrate-477217"],
    capture_output=True, text=True
)
raw_url = result.stdout.strip()
# Format: postgresql://postgres:PASS@/postgres?host=/cloudsql/...
# Extract password
import re
m = re.match(r"postgresql://([^:]+):([^@]+)@/([^?]+)", raw_url)
PG_USER = m.group(1)
PG_PASS = m.group(2)
PG_DB   = m.group(3)
PG_HOST = "35.232.231.189"   # Cloud SQL public IP

print(f"Connecting to {PG_HOST} db={PG_DB} user={PG_USER}", flush=True)

conn = psycopg2.connect(
    host=PG_HOST, port=5432,
    user=PG_USER, password=PG_PASS,
    dbname=PG_DB,
    connect_timeout=10,
    options="-c statement_timeout=60000"
)
conn.autocommit = True
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def q(sql, params=None):
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]

def sep(title):
    print("\n" + "="*72, flush=True)
    print(f"  {title}", flush=True)
    print("="*72, flush=True)

def fmt(rows):
    for r in rows:
        print(json.dumps({k: str(v) if not isinstance(v, (int, float, bool, type(None))) else v
                          for k, v in r.items()}, ensure_ascii=False), flush=True)

# ═══════════════════════════════════════════════════════════════════════
# INVENTÁRIO GERAL
# ═══════════════════════════════════════════════════════════════════════
sep("INVENTÁRIO GERAL")

r = q("""
    SELECT
        (SELECT COUNT(*) FROM shadow_trades) AS st_total,
        (SELECT COUNT(*) FROM shadow_trades WHERE pnl_pct IS NOT NULL) AS st_with_pnl,
        (SELECT COUNT(DISTINCT symbol) FROM shadow_trades) AS st_symbols,
        (SELECT MIN(created_at) FROM shadow_trades) AS st_date_min,
        (SELECT MAX(created_at) FROM shadow_trades) AS st_date_max
""")
fmt(r)

r = q("""
    SELECT
        (SELECT COUNT(*) FROM decisions_log WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')) AS dl_l3_total,
        (SELECT COUNT(*) FROM decisions_log WHERE l3_pass=true AND decision='ALLOW') AS dl_allow,
        (SELECT COUNT(*) FROM decisions_log WHERE l3_pass=true AND decision='BLOCK') AS dl_block,
        (SELECT COUNT(*) FROM decisions_log WHERE l3_pass=true AND decision='ALLOW' AND outcome IN ('tp','sl')) AS dl_allow_tp_sl,
        (SELECT COUNT(*) FROM decisions_log WHERE l3_pass=true AND decision='ALLOW' AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL) AS dl_allow_tp_sl_pnl
""")
fmt(r)

sep("SHADOW TRADES POR SOURCE+OUTCOME")
r = q("""
    SELECT source, outcome,
           COUNT(*) AS n,
           SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS with_pnl,
           SUM(CASE WHEN pnl_pct IS NULL THEN 1 ELSE 0 END) AS no_pnl,
           ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl
    FROM shadow_trades
    GROUP BY source, outcome
    ORDER BY source, outcome
""")
fmt(r)

# ═══════════════════════════════════════════════════════════════════════
# FASE 1 — FUNIL COMPLETO
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 1 — FUNIL COMPLETO: SHADOW → TRAIN → VAL → TEST")

r = q("""
SELECT
  -- S1: Shadow total com pnl
  (SELECT COUNT(*) FROM shadow_trades WHERE pnl_pct IS NOT NULL) AS s1_shadow_with_pnl,

  -- S2: Shadow que tem decision_id correspondente em decisions_log
  (SELECT COUNT(DISTINCT st.id) FROM shadow_trades st
   JOIN decisions_log dl ON dl.id = st.decision_id
   WHERE st.pnl_pct IS NOT NULL) AS s2_shadow_matched_dl,

  -- S3: decisions_log ALLOW+BLOCK, l3_pass, tp/sl, pnl (todos os tempos)
  (SELECT COUNT(*) FROM decisions_log
   WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
     AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL) AS s3_dl_all_time_allow_block,

  -- S3b: só ALLOW
  (SELECT COUNT(*) FROM decisions_log
   WHERE l3_pass=true AND decision='ALLOW'
     AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL) AS s3b_dl_all_time_allow_only,

  -- S4: dentro da janela 90 dias ALLOW+BLOCK
  (SELECT COUNT(*) FROM decisions_log
   WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
     AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
     AND created_at >= NOW() - INTERVAL '90 days') AS s4_dl_90d_allow_block,

  -- S4b: só ALLOW (config padrão INCLUDE_REJECTED=false)
  (SELECT COUNT(*) FROM decisions_log
   WHERE l3_pass=true AND decision='ALLOW'
     AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
     AND created_at >= NOW() - INTERVAL '90 days') AS s4b_dl_90d_allow_only,

  -- S5: após filtro de data May1-20 excluído, ALLOW+BLOCK
  (SELECT COUNT(*) FROM decisions_log
   WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
     AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
     AND created_at >= NOW() - INTERVAL '90 days'
     AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
  ) AS s5_after_date_excl_allow_block,

  -- S5b: só ALLOW
  (SELECT COUNT(*) FROM decisions_log
   WHERE l3_pass=true AND decision='ALLOW'
     AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
     AND created_at >= NOW() - INTERVAL '90 days'
     AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
  ) AS s5b_after_date_excl_allow_only,

  -- S6: após DISTINCT ON (symbol, DATE), ALLOW+BLOCK
  (SELECT COUNT(*) FROM (
    SELECT DISTINCT ON (symbol, DATE(created_at)) id
    FROM decisions_log
    WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
    ORDER BY symbol, DATE(created_at), created_at ASC
  ) d) AS s6_after_dedup_allow_block,

  -- S6b: só ALLOW
  (SELECT COUNT(*) FROM (
    SELECT DISTINCT ON (symbol, DATE(created_at)) id
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
    ORDER BY symbol, DATE(created_at), created_at ASC
  ) d) AS s6b_after_dedup_allow_only
""")
fmt(r)

# ═══════════════════════════════════════════════════════════════════════
# FASE 2 — MOTIVOS DE EXCLUSÃO (ranking)
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 2 — FUNIL STEP-BY-STEP COM PERDAS")

# Todos os shadow trades como ponto de partida
r_shadow_total = q("SELECT COUNT(*) AS n FROM shadow_trades")[0]["n"]
r_shadow_pnl   = q("SELECT COUNT(*) AS n FROM shadow_trades WHERE pnl_pct IS NOT NULL")[0]["n"]

# decisions_log candidatos base (sem filtro de data)
r_dl_base = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
""")[0]["n"]

# Quantos shadows NÃO têm decision_id correspondente (MISSING_DECISION)
r_missing_dl = q("""
    SELECT COUNT(DISTINCT st.id) AS n
    FROM shadow_trades st
    LEFT JOIN decisions_log dl ON dl.id = st.decision_id
    WHERE st.pnl_pct IS NOT NULL AND dl.id IS NULL
""")[0]["n"]

# Quantos decisions_log ALLOW tp/sl NÃO têm metrics
r_no_metrics = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND (metrics IS NULL OR metrics::text='{}')
""")[0]["n"]

# Filtro de data: fora da janela 90d
r_too_old = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at < NOW() - INTERVAL '90 days'
""")[0]["n"]

# Filtro May1-20
r_may_excl = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59'
""")[0]["n"]

# Removidos por DISTINCT ON (deduplicação)
r_before_dedup = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")[0]["n"]

r_after_dedup = q("""
    SELECT COUNT(*) AS n FROM (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ) d
""")[0]["n"]
r_dedup_removed = r_before_dedup - r_after_dedup

# BLOCK excluídos (quando INCLUDE_REJECTED=false — config padrão não é esse, mas é histórico)
r_block_excluded = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='BLOCK'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")[0]["n"]

# Estimativa NaN drop (MAX_NAN_FRACTION=0.5, 25 FEATURE_COLUMNS aproximado)
r_nan_drop = q("""
    WITH deduped AS (
        SELECT DISTINCT ON (symbol, DATE(created_at))
            id, metrics
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ),
    null_counts AS (
        SELECT id,
            (CASE WHEN (metrics->>'taker_ratio') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'volume_delta') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'rsi') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'adx') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'spread_pct') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'macd_histogram_pct') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'vwap_distance_pct') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'volume_spike') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'bb_width') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'ema9_gt_ema21') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'ema50_gt_ema200') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'volume_24h_usdt') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'orderbook_depth_usdt') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'ema9') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'ema21') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'ema50') IS NULL THEN 1 ELSE 0 END +
             CASE WHEN (metrics->>'ema200') IS NULL THEN 1 ELSE 0 END
            ) AS null_count
        FROM deduped
    )
    SELECT
        SUM(CASE WHEN null_count > 8 THEN 1 ELSE 0 END) AS would_drop_gt50pct_17cols,
        SUM(CASE WHEN null_count = 0 THEN 1 ELSE 0 END) AS all_present,
        COUNT(*) AS total
    FROM null_counts
""")
fmt(r_nan_drop)
r_nan_drop_n = r_nan_drop[0]["would_drop_gt50pct_17cols"] or 0
r_final_dataset = r_after_dedup  # before NaN drop
r_estimated_final = r_after_dedup - r_nan_drop_n

print(f"\n--- FUNIL ---", flush=True)
print(f"S1  Shadow total                     : {r_shadow_total}", flush=True)
print(f"S2  Shadow com pnl                   : {r_shadow_pnl} (perda: {r_shadow_total - r_shadow_pnl})", flush=True)
print(f"S3  decisions_log ALLOW tp/sl pnl    : {r_dl_base} (todos os tempos)", flush=True)
print(f"    Shadows sem decision_id em DL    : {r_missing_dl}", flush=True)
print(f"    Decisions sem metrics            : {r_no_metrics}", flush=True)
print(f"S4  Após filtro 90 dias (old)        : perda {r_too_old}", flush=True)
print(f"S5  Após exclusão May1-20            : perda {r_may_excl}", flush=True)
print(f"S5b Antes dedup (90d excl.May)       : {r_before_dedup}", flush=True)
print(f"S6  Após DISTINCT ON (dedup)         : {r_after_dedup} (removidos: {r_dedup_removed})", flush=True)
print(f"    BLOCK excluídos (sem REJECTED)   : {r_block_excluded}", flush=True)
print(f"    Est. NaN drop (>50% NaN)         : {r_nan_drop_n}", flush=True)
print(f"    Dataset final estimado           : {r_estimated_final}", flush=True)
# Train/Val/Test split 70/15/15
n = r_estimated_final
print(f"    → Train (70%): {int(n*0.70)} | Val (15%): {int(n*0.15)} | Test (15%): {int(n*0.15)}", flush=True)

# ═══════════════════════════════════════════════════════════════════════
# FASE 3 — DISTINCT ON INVESTIGATION
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 3 — DISTINCT ON INVESTIGATION")

r = q("""
    SELECT
        COUNT(*) AS total_before_dedup,
        COUNT(DISTINCT (symbol, DATE(created_at))) AS distinct_symbol_date_pairs,
        COUNT(*) - COUNT(DISTINCT (symbol, DATE(created_at))) AS rows_removed
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")
fmt(r)

print("\nTOP 30 symbol/date mais impactados pelo DISTINCT:", flush=True)
r = q("""
    SELECT symbol, DATE(created_at) AS trade_date, COUNT(*) AS n_rows,
           MIN(created_at) AS earliest, MAX(created_at) AS latest
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
    GROUP BY symbol, DATE(created_at)
    HAVING COUNT(*) > 1
    ORDER BY n_rows DESC
    LIMIT 30
""")
fmt(r)

print("\nTOP 20 symbols por total de trades removidos pelo DISTINCT:", flush=True)
r = q("""
    SELECT symbol,
           COUNT(*) AS total_rows,
           COUNT(DISTINCT DATE(created_at)) AS distinct_days,
           COUNT(*) - COUNT(DISTINCT DATE(created_at)) AS removed_by_dedup
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
    GROUP BY symbol
    HAVING COUNT(*) > COUNT(DISTINCT DATE(created_at))
    ORDER BY removed_by_dedup DESC
    LIMIT 20
""")
fmt(r)

# Critério de seleção: qual trade sobrevive?
print("\nCritério do DISTINCT: ORDER BY symbol, DATE(created_at), created_at ASC → sobrevive o mais ANTIGO do dia", flush=True)

# ═══════════════════════════════════════════════════════════════════════
# FASE 4 — BLOCK COVERAGE
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 4 — BLOCK COVERAGE (INCLUDE_REJECTED_IN_TRAIN=true)")

r = q("""
    SELECT
        decision,
        COUNT(*) AS total,
        SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS has_pnl,
        SUM(CASE WHEN pnl_pct IS NULL THEN 1 ELSE 0 END) AS no_pnl,
        SUM(CASE WHEN outcome IN ('tp','sl') THEN 1 ELSE 0 END) AS tp_sl_outcome,
        SUM(CASE WHEN outcome='timeout' THEN 1 ELSE 0 END) AS timeout_outcome,
        SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END) AS null_outcome,
        SUM(CASE WHEN metrics IS NOT NULL AND metrics::text != '{}' THEN 1 ELSE 0 END) AS has_metrics
    FROM decisions_log
    WHERE l3_pass=true
      AND created_at >= NOW() - INTERVAL '90 days'
    GROUP BY decision
    ORDER BY decision
""")
fmt(r)

# BLOCK que chegaria ao dataset com INCLUDE_REJECTED=true
r = q("""
    SELECT COUNT(*) AS block_in_dataset_with_included_rejected
    FROM (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id
        FROM decisions_log
        WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ) d
    JOIN decisions_log dl ON dl.id = d.id
    WHERE dl.decision = 'BLOCK'
""")
fmt(r)

# BLOCK pnl_pct está em decisions_log?
r = q("""
    SELECT
        SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS block_has_pnl_in_dl,
        SUM(CASE WHEN pnl_pct IS NULL THEN 1 ELSE 0 END) AS block_no_pnl_in_dl,
        COUNT(*) AS block_total_90d,
        ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl
    FROM decisions_log
    WHERE l3_pass=true AND decision='BLOCK'
      AND created_at >= NOW() - INTERVAL '90 days'
""")
fmt(r)

# Shadow L3_REJECTED
r = q("""
    SELECT
        COUNT(*) AS total_l3_rejected_shadows,
        SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS with_pnl,
        SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) AS tp_hit,
        SUM(CASE WHEN outcome='SL_HIT' THEN 1 ELSE 0 END) AS sl_hit,
        ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl
    FROM shadow_trades
    WHERE source='L3_REJECTED'
      AND created_at >= NOW() - INTERVAL '90 days'
""")
fmt(r)

# ═══════════════════════════════════════════════════════════════════════
# FASE 5 — PERÍODO EXCLUÍDO May 1-20
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 5 — PERÍODO EXCLUÍDO (2026-05-01 a 2026-05-20)")

r = q("""
    SELECT
        decision,
        outcome,
        COUNT(*) AS n,
        SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS has_pnl
    FROM decisions_log
    WHERE l3_pass=true
      AND created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59'
      AND created_at >= NOW() - INTERVAL '90 days'
    GROUP BY decision, outcome
    ORDER BY decision, outcome
""")
fmt(r)

r = q("""
    SELECT
        COUNT(*) AS total_excl_period,
        COUNT(*) FILTER (WHERE decision='ALLOW') AS allow,
        COUNT(*) FILTER (WHERE decision='BLOCK') AS block,
        COUNT(*) FILTER (WHERE outcome='tp') AS tp,
        COUNT(*) FILTER (WHERE outcome='sl') AS sl,
        COUNT(*) FILTER (WHERE outcome='timeout') AS timeout,
        COUNT(*) FILTER (WHERE pnl_pct IS NOT NULL) AS has_pnl
    FROM decisions_log
    WHERE l3_pass=true
      AND created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59'
      AND created_at >= NOW() - INTERVAL '90 days'
      AND outcome IN ('tp','sl')
""")
fmt(r)

# ═══════════════════════════════════════════════════════════════════════
# FASE 6 — COBERTURA POR RESULTADO
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 6 — COVERAGE POR RESULTADO: SHADOW vs DATASET ML")

print("\n--- Shadow Portfolio por outcome ---", flush=True)
r = q("""
    SELECT outcome,
           COUNT(*) AS n_shadow,
           SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS with_pnl,
           ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl
    FROM shadow_trades
    GROUP BY outcome
    ORDER BY outcome
""")
fmt(r)

print("\n--- decisions_log ALLOW/BLOCK por outcome (90d, excl. May1-20, pré-dedup) ---", flush=True)
r = q("""
    SELECT decision, outcome,
           COUNT(*) AS n_dl,
           ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl
    FROM decisions_log
    WHERE l3_pass=true
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
    GROUP BY decision, outcome
    ORDER BY decision, outcome
""")
fmt(r)

print("\n--- Dataset ML (pós-dedup) por outcome ---", flush=True)
r = q("""
    WITH deduped AS (
        SELECT DISTINCT ON (symbol, DATE(created_at))
            id, outcome, pnl_pct
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    )
    SELECT outcome,
           COUNT(*) AS n_ml,
           ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl,
           SUM(CASE WHEN pnl_pct > 0.96 THEN 1 ELSE 0 END) AS label_win,
           SUM(CASE WHEN pnl_pct <= 0.96 THEN 1 ELSE 0 END) AS label_loss
    FROM deduped
    GROUP BY outcome
    ORDER BY outcome
""")
fmt(r)

# ═══════════════════════════════════════════════════════════════════════
# FASE 7 — TRADE TRACE (100 aleatórios)
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 7 — TRADE TRACE (100 trades aleatórios do Shadow)")

r = q("""
    WITH sample AS (
        SELECT st.id AS shadow_id,
               st.symbol,
               st.decision_id,
               st.pnl_pct AS shadow_pnl,
               st.outcome AS shadow_outcome,
               st.created_at AS shadow_created_at,
               st.source
        FROM shadow_trades st
        WHERE st.created_at >= NOW() - INTERVAL '90 days'
        ORDER BY random()
        LIMIT 100
    ),
    dl_info AS (
        SELECT dl.id AS dl_id,
               dl.symbol,
               dl.decision,
               dl.outcome AS dl_outcome,
               dl.pnl_pct AS dl_pnl,
               dl.metrics IS NOT NULL AND dl.metrics::text != '{}' AS has_metrics,
               dl.created_at AS dl_created_at,
               dl.l3_pass
        FROM decisions_log dl
        WHERE dl.l3_pass=true
    ),
    deduped_ids AS (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    )
    SELECT
        s.shadow_id,
        s.symbol,
        s.source,
        s.shadow_outcome,
        s.shadow_pnl,
        dl.dl_id IS NOT NULL AS decision_present,
        dl.decision,
        dl.dl_outcome,
        dl.dl_pnl IS NOT NULL AS pnl_present,
        dl.has_metrics,
        dl.dl_outcome IN ('tp','sl') AS valid_outcome,
        dl.l3_pass,
        -- Is in 90d window?
        (dl.dl_created_at >= NOW() - INTERVAL '90 days') AS within_90d,
        -- Is in May exclusion?
        (dl.dl_created_at >= '2026-05-01' AND dl.dl_created_at <= '2026-05-20 23:59:59') AS in_may_excl,
        -- Survived dedup?
        (dedup.id IS NOT NULL) AS survived_dedup,
        CASE
            WHEN dl.dl_id IS NULL THEN 'MISSING_DECISION'
            WHEN NOT dl.l3_pass THEN 'L3_FAIL'
            WHEN dl.decision NOT IN ('ALLOW','BLOCK') THEN 'WRONG_DECISION_TYPE'
            WHEN dl.dl_outcome NOT IN ('tp','sl') THEN 'NO_LABEL'
            WHEN dl.dl_pnl IS NULL THEN 'MISSING_PNL'
            WHEN dl.dl_created_at < NOW() - INTERVAL '90 days' THEN 'FILTERED_BY_DATE'
            WHEN (dl.dl_created_at >= '2026-05-01' AND dl.dl_created_at <= '2026-05-20 23:59:59') THEN 'FILTERED_BY_DATE_MAY'
            WHEN dl.decision = 'BLOCK' THEN 'BLOCK_NOT_SUPPORTED_OLD'
            WHEN NOT dl.has_metrics THEN 'MISSING_METRICS'
            WHEN dedup.id IS NULL THEN 'FILTERED_BY_DEDUP'
            ELSE 'IN_DATASET'
        END AS exclusion_reason
    FROM sample s
    LEFT JOIN dl_info dl ON dl.dl_id = s.decision_id
    LEFT JOIN deduped_ids dedup ON dedup.id = s.decision_id
    ORDER BY s.symbol
""")
fmt(r)

# Resumo dos motivos
from collections import Counter
reasons = Counter(row["exclusion_reason"] for row in r)
print("\n--- Resumo motivos de exclusão (100 amostras) ---", flush=True)
for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
    print(f"  {reason}: {cnt} ({cnt}%)", flush=True)

# ═══════════════════════════════════════════════════════════════════════
# FASE 8 — RECONCILIAÇÃO TOTAL
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 8 — RECONCILIAÇÃO TOTAL E RANKING DE CAUSAS")

# Rastrear TODOS os shadow trades e categorizar
r = q("""
    WITH all_shadows AS (
        SELECT
            st.id AS shadow_id,
            st.decision_id,
            st.pnl_pct AS shadow_pnl,
            st.created_at AS shadow_date,
            st.source
        FROM shadow_trades st
        WHERE st.created_at >= NOW() - INTERVAL '90 days'
    ),
    dl_join AS (
        SELECT
            s.shadow_id,
            s.decision_id,
            s.shadow_pnl,
            s.source,
            dl.id AS dl_id,
            dl.decision,
            dl.outcome,
            dl.pnl_pct AS dl_pnl,
            dl.l3_pass,
            dl.created_at AS dl_created_at,
            dl.metrics IS NOT NULL AND dl.metrics::text != '{}' AS has_metrics
        FROM all_shadows s
        LEFT JOIN decisions_log dl ON dl.id = s.decision_id
    ),
    deduped_ids AS (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    )
    SELECT
        CASE
            WHEN j.dl_id IS NULL THEN 'MISSING_DECISION'
            WHEN j.shadow_pnl IS NULL THEN 'MISSING_PNL_SHADOW'
            WHEN NOT j.l3_pass THEN 'L3_FAIL'
            WHEN j.outcome NOT IN ('tp','sl') THEN 'NO_LABEL'
            WHEN j.dl_pnl IS NULL THEN 'MISSING_PNL'
            WHEN j.dl_created_at < NOW() - INTERVAL '90 days' THEN 'FILTERED_BY_DATE_90D'
            WHEN (j.dl_created_at >= '2026-05-01' AND j.dl_created_at <= '2026-05-20 23:59:59') THEN 'FILTERED_BY_DATE_MAY'
            WHEN j.decision = 'BLOCK' THEN 'BLOCK_EXCLUDED'
            WHEN NOT j.has_metrics THEN 'MISSING_METRICS'
            WHEN dedup.id IS NULL THEN 'FILTERED_BY_DEDUP'
            ELSE 'IN_DATASET'
        END AS exclusion_reason,
        COUNT(*) AS n
    FROM dl_join j
    LEFT JOIN deduped_ids dedup ON dedup.id = j.decision_id
    GROUP BY 1
    ORDER BY n DESC
""")
fmt(r)

# ═══════════════════════════════════════════════════════════════════════
# FASE 9 — SIMULAÇÃO TEÓRICA
# ═══════════════════════════════════════════════════════════════════════
sep("FASE 9 — SIMULAÇÃO TEÓRICA")

# Cenário A: dataset atual (ALLOW, 90d, excl May, dedup)
r_a = r_after_dedup

# Cenário B: sem DISTINCT (todos os rows antes do dedup)
r_b = r_before_dedup

# Cenário C: com BLOCK incluído (ALLOW+BLOCK, pós dedup)
r_c = q("""
    SELECT COUNT(*) AS n FROM (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id
        FROM decisions_log
        WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ) d
""")[0]["n"]

# Cenário D: ALLOW+BLOCK sem DISTINCT
r_d = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")[0]["n"]

# Cenário E: completo (sem filtro de data, sem dedup, só ALLOW tp/sl pnl)
r_e = q("""
    SELECT COUNT(*) AS n FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
""")[0]["n"]

print(f"\nCenário A (ATUAL):             {r_a} registros (ALLOW, 90d, excl May, DISTINCT)", flush=True)
print(f"Cenário B (sem DISTINCT):      {r_b} registros (ALLOW, 90d, excl May, SEM DISTINCT)", flush=True)
print(f"Cenário C (com BLOCK):         {r_c} registros (ALLOW+BLOCK, 90d, excl May, DISTINCT)", flush=True)
print(f"Cenário D (BLOCK+sem DISTINCT):{r_d} registros (ALLOW+BLOCK, 90d, excl May, SEM DISTINCT)", flush=True)
print(f"Cenário E (completo histórico):{r_e} registros (ALLOW, todos os tempos, SEM DISTINCT)", flush=True)

# ═══════════════════════════════════════════════════════════════════════
# FILTRO MENSAL — distribuição
# ═══════════════════════════════════════════════════════════════════════
sep("DISTRIBUIÇÃO MENSAL")

r = q("""
    SELECT
        DATE_TRUNC('month', created_at) AS month,
        COUNT(*) AS n_raw,
        COUNT(DISTINCT (symbol, DATE(created_at))) AS n_after_dedup
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
    GROUP BY 1 ORDER BY 1
""")
fmt(r)

cur.close()
conn.close()
print("\n=== AUDIT COMPLETE ===", flush=True)
