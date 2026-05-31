"""
SQL Forensic Reconciliation Audit
Runs via Cloud Run Job override:
  gcloud run jobs execute scalpyn-ml-trainer \
    --override-env AUDIT_MODE=sql_audit \
    --region=us-central1 --project=clickrate-477217
"""

import os, sys, json, logging
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SQL_AUDIT] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("sql_audit")
DB_URL = os.environ["DB_URL"]
engine = create_engine(DB_URL, pool_pre_ping=True)

def q(sql, params=None):
    with engine.connect() as c:
        r = c.execute(text(sql), params or {})
        return [dict(row._mapping) for row in r.fetchall()]

def sep(title):
    log.info("=" * 72)
    log.info("  %s", title)
    log.info("=" * 72)

# ─── FASE 1: INVENTÁRIO ───────────────────────────────────────────────────────
sep("FASE 1 — INVENTÁRIO COMPLETO")

for tbl, extra in [
    ("decisions_log", "l3_pass=true AND decision IN ('ALLOW','BLOCK')"),
    ("shadow_trades", "1=1"),
    ("ml_predictions", "1=1"),
    ("ml_models", "1=1"),
]:
    rows = q(f"""
        SELECT
            COUNT(*) AS total_rows,
            MIN(created_at) AS date_min,
            MAX(created_at) AS date_max,
            COUNT(DISTINCT symbol) AS distinct_symbols
        FROM {tbl}
        WHERE {extra}
    """)
    log.info("[%s] %s", tbl, json.dumps(rows[0], default=str))

# decisions_log detail
r = q("""
    SELECT decision, outcome, COUNT(*) AS n
    FROM decisions_log
    WHERE l3_pass=true
    GROUP BY decision, outcome
    ORDER BY decision, outcome
""")
log.info("[decisions_log by decision+outcome] %s", json.dumps(r, default=str))

# shadow_trades detail
r = q("""
    SELECT source, outcome,
           COUNT(*) AS n,
           SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS has_pnl,
           SUM(CASE WHEN pnl_pct IS NULL THEN 1 ELSE 0 END) AS null_pnl
    FROM shadow_trades
    GROUP BY source, outcome
    ORDER BY source, outcome
""")
log.info("[shadow_trades by source+outcome] %s", json.dumps(r, default=str))

# ─── FASE 2: SCHEMA / CHAVES ─────────────────────────────────────────────────
sep("FASE 2 — SCHEMA E CHAVES")

for tbl in ["decisions_log", "shadow_trades", "ml_predictions"]:
    r = q(f"""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name='{tbl}'
        ORDER BY ordinal_position
    """)
    log.info("[schema:%s] %s", tbl, json.dumps(r, default=str))

# Relacionamento shadow_trades → decisions_log
r = q("""
    SELECT
        COUNT(*) AS total_shadows,
        SUM(CASE WHEN decision_id IS NOT NULL THEN 1 ELSE 0 END) AS has_decision_id,
        SUM(CASE WHEN decision_id IS NULL THEN 1 ELSE 0 END) AS null_decision_id
    FROM shadow_trades
""")
log.info("[shadow FK decision_id coverage] %s", json.dumps(r[0], default=str))

# ─── FASE 3: RECONCILIAÇÃO SHADOW vs DECISIONS_LOG ───────────────────────────
sep("FASE 3 — RECONCILIAÇÃO SHADOW vs DECISIONS_LOG")

# Shadows com decision_id que existe em decisions_log
r = q("""
    SELECT
        COUNT(DISTINCT st.id) AS shadows_matched,
        COUNT(DISTINCT st.id) FILTER (WHERE dl.id IS NULL) AS shadows_missing_in_dl,
        COUNT(DISTINCT st.id) FILTER (WHERE dl.id IS NOT NULL) AS shadows_found_in_dl
    FROM shadow_trades st
    LEFT JOIN decisions_log dl ON dl.id = st.decision_id
    WHERE st.pnl_pct IS NOT NULL
""")
log.info("[shadow vs decisions_log reconciliation] %s", json.dumps(r[0], default=str))

# Decisions_log ALLOW com outcome tp/sl sem shadow trade
r = q("""
    SELECT
        COUNT(DISTINCT dl.id) AS dl_allow_tp_sl,
        COUNT(DISTINCT st.decision_id) AS have_shadow,
        COUNT(DISTINCT dl.id) FILTER (WHERE st.id IS NULL) AS missing_shadow
    FROM decisions_log dl
    LEFT JOIN shadow_trades st ON st.decision_id = dl.id
    WHERE dl.l3_pass=true AND dl.decision='ALLOW'
      AND dl.outcome IN ('tp','sl') AND dl.pnl_pct IS NOT NULL
""")
log.info("[decisions_log ALLOW tp/sl vs shadow] %s", json.dumps(r[0], default=str))

# Amostra de shadows sem decisions_log
r = q("""
    SELECT st.id, st.symbol, st.source, st.decision_id, st.pnl_pct, st.outcome, st.created_at
    FROM shadow_trades st
    LEFT JOIN decisions_log dl ON dl.id = st.decision_id
    WHERE dl.id IS NULL AND st.pnl_pct IS NOT NULL
    LIMIT 10
""")
log.info("[sample shadows missing in decisions_log] %s", json.dumps(r, default=str))

# Amostra de decisions_log ALLOW sem shadow
r = q("""
    SELECT dl.id, dl.symbol, dl.decision, dl.outcome, dl.pnl_pct, dl.created_at
    FROM decisions_log dl
    LEFT JOIN shadow_trades st ON st.decision_id = dl.id
    WHERE dl.l3_pass=true AND dl.decision='ALLOW'
      AND dl.outcome IN ('tp','sl') AND dl.pnl_pct IS NOT NULL
      AND st.id IS NULL
    LIMIT 10
""")
log.info("[sample decisions missing shadow] %s", json.dumps(r, default=str))

# ─── FASE 4: RECONCILIAÇÃO SHADOW vs DATASET ML ───────────────────────────────
sep("FASE 4 — FUNIL: SHADOW → DECISIONS_LOG → DATASET ML")

# Funil step-by-step
r = q("""
    SELECT COUNT(*) AS total_shadow_trades FROM shadow_trades WHERE pnl_pct IS NOT NULL
""")
log.info("[F1] shadow_trades with pnl: %s", r[0])

r = q("""
    SELECT COUNT(*) AS in_decisions_with_pnl
    FROM shadow_trades st
    JOIN decisions_log dl ON dl.id = st.decision_id
    WHERE st.pnl_pct IS NOT NULL AND dl.pnl_pct IS NOT NULL
""")
log.info("[F2] shadow matched decisions_log with pnl: %s", r[0])

r = q("""
    SELECT COUNT(*) AS dl_l3_pass_allow_tp_sl_pnl
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
""")
log.info("[F3] decisions_log raw candidates (ALLOW, l3_pass, tp/sl, has pnl): %s", r[0])

r = q("""
    SELECT COUNT(*) AS dl_with_block
    FROM decisions_log
    WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK')
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
""")
log.info("[F3b] decisions_log ALLOW+BLOCK, tp/sl, has pnl: %s", r[0])

# After date filter
r = q("""
    SELECT COUNT(*) AS after_date_filter
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")
log.info("[F4] after 90d window + exclude May1-20: %s", r[0])

# After dedup DISTINCT ON (symbol, DATE)
r = q("""
    SELECT COUNT(*) AS after_dedup
    FROM (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ) d
""")
log.info("[F5] after DISTINCT ON (symbol, DATE): %s", r[0])

# With metrics not null
r = q("""
    SELECT COUNT(*) AS has_metrics
    FROM (
        SELECT DISTINCT ON (symbol, DATE(created_at)) id, metrics
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ) d
    WHERE metrics IS NOT NULL AND metrics::text != '{}'
""")
log.info("[F6] with non-empty metrics: %s", r[0])

# ─── FASE 5: DEDUPLICAÇÃO DETAIL ─────────────────────────────────────────────
sep("FASE 5 — DEDUPLICAÇÃO DETAIL")

r = q("""
    SELECT
        COUNT(*) AS total_before_dedup,
        COUNT(DISTINCT (symbol, DATE(created_at))) AS distinct_symbol_date_pairs,
        COUNT(*) - COUNT(DISTINCT (symbol, DATE(created_at))) AS rows_removed_by_dedup
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")
log.info("[dedup impact] %s", json.dumps(r[0], default=str))

# Top symbols with most duplication
r = q("""
    SELECT symbol, DATE(created_at) AS trade_date, COUNT(*) AS n_rows
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
    GROUP BY symbol, DATE(created_at)
    HAVING COUNT(*) > 1
    ORDER BY n_rows DESC
    LIMIT 20
""")
log.info("[top deduplicated symbol/date pairs] %s", json.dumps(r, default=str))

# ─── FASE 6: FILTRO DE DATA ───────────────────────────────────────────────────
sep("FASE 6 — FILTRO DE DATA")

r = q("""
    SELECT
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '90 days') AS within_90d,
        COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '90 days') AS older_than_90d,
        COUNT(*) FILTER (
            WHERE created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59'
              AND created_at >= NOW() - INTERVAL '90 days'
        ) AS excluded_may1_20,
        COUNT(*) FILTER (
            WHERE created_at >= NOW() - INTERVAL '90 days'
              AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ) AS after_all_date_filters
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
""")
log.info("[date filter breakdown] %s", json.dumps(r[0], default=str))

# Monthly distribution
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
log.info("[monthly distribution ALLOW tp/sl pnl] %s", json.dumps(r, default=str))

# ─── FASE 7: AUDITORIA NULL METRICS / NULL PNL ────────────────────────────────
sep("FASE 7 — NULL METRICS / NULL PNL / NaN FEATURES")

r = q("""
    SELECT
        COUNT(*) AS total_decisions,
        SUM(CASE WHEN pnl_pct IS NULL THEN 1 ELSE 0 END) AS null_pnl,
        SUM(CASE WHEN metrics IS NULL THEN 1 ELSE 0 END) AS null_metrics,
        SUM(CASE WHEN metrics::text = '{}' THEN 1 ELSE 0 END) AS empty_metrics,
        SUM(CASE WHEN metrics IS NOT NULL AND metrics::text != '{}' THEN 1 ELSE 0 END) AS has_metrics
    FROM decisions_log
    WHERE l3_pass=true AND decision='ALLOW'
      AND outcome IN ('tp','sl')
      AND created_at >= NOW() - INTERVAL '90 days'
      AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
""")
log.info("[null analysis on filtered set] %s", json.dumps(r[0], default=str))

# Key feature coverage on the deduped set
r = q("""
    WITH deduped AS (
        SELECT DISTINCT ON (symbol, DATE(created_at))
            id, metrics
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    )
    SELECT
        COUNT(*) AS n,
        SUM(CASE WHEN metrics IS NULL OR metrics::text='{}' THEN 1 ELSE 0 END) AS no_metrics,
        SUM(CASE WHEN (metrics->>'taker_ratio') IS NULL THEN 1 ELSE 0 END) AS null_taker_ratio,
        SUM(CASE WHEN (metrics->>'volume_delta') IS NULL THEN 1 ELSE 0 END) AS null_volume_delta,
        SUM(CASE WHEN (metrics->>'rsi') IS NULL THEN 1 ELSE 0 END) AS null_rsi,
        SUM(CASE WHEN (metrics->>'adx') IS NULL THEN 1 ELSE 0 END) AS null_adx,
        SUM(CASE WHEN (metrics->>'spread_pct') IS NULL THEN 1 ELSE 0 END) AS null_spread_pct,
        SUM(CASE WHEN (metrics->>'macd_histogram_pct') IS NULL THEN 1 ELSE 0 END) AS null_macd_hist_pct,
        SUM(CASE WHEN (metrics->>'vwap_distance_pct') IS NULL THEN 1 ELSE 0 END) AS null_vwap_dist_pct,
        SUM(CASE WHEN (metrics->>'volume_spike') IS NULL THEN 1 ELSE 0 END) AS null_vol_spike,
        SUM(CASE WHEN (metrics->>'bb_width') IS NULL THEN 1 ELSE 0 END) AS null_bb_width,
        SUM(CASE WHEN (metrics->>'ema9_gt_ema21') IS NULL THEN 1 ELSE 0 END) AS null_ema9_gt_ema21,
        SUM(CASE WHEN (metrics->>'ema50_gt_ema200') IS NULL THEN 1 ELSE 0 END) AS null_ema50_gt_ema200,
        SUM(CASE WHEN (metrics->>'volume_24h_usdt') IS NULL THEN 1 ELSE 0 END) AS null_vol24h,
        SUM(CASE WHEN (metrics->>'orderbook_depth_usdt') IS NULL THEN 1 ELSE 0 END) AS null_ob_depth,
        SUM(CASE WHEN (metrics->>'ema9') IS NULL THEN 1 ELSE 0 END) AS null_ema9,
        SUM(CASE WHEN (metrics->>'ema21') IS NULL THEN 1 ELSE 0 END) AS null_ema21,
        SUM(CASE WHEN (metrics->>'ema50') IS NULL THEN 1 ELSE 0 END) AS null_ema50,
        SUM(CASE WHEN (metrics->>'ema200') IS NULL THEN 1 ELSE 0 END) AS null_ema200,
        SUM(CASE WHEN (metrics->>'close') IS NULL AND (metrics->>'price') IS NULL THEN 1 ELSE 0 END) AS null_close_price
    FROM deduped
""")
log.info("[feature coverage in deduped set] %s", json.dumps(r[0], default=str))

# Count rows with more than 50% NaN across all feature columns (approximation via null count)
r = q("""
    WITH deduped AS (
        SELECT DISTINCT ON (symbol, DATE(created_at))
            id, metrics, symbol, DATE(created_at) AS trade_date
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    ),
    null_counts AS (
        SELECT id, symbol, trade_date,
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
        SUM(CASE WHEN null_count > 8 THEN 1 ELSE 0 END) AS would_drop_50pct_17cols,
        SUM(CASE WHEN null_count = 0 THEN 1 ELSE 0 END) AS all_present,
        SUM(CASE WHEN metrics IS NULL OR metrics::text='{}' THEN 1 ELSE 0 END) AS no_metrics_at_all,
        COUNT(*) AS total
    FROM null_counts n
    JOIN deduped d ON d.id = n.id
""")
log.info("[NaN drop estimation on 17 key cols] %s", json.dumps(r[0], default=str))

# ─── FASE 8: LABELS — SHADOW OUTCOME vs ML LABEL ─────────────────────────────
sep("FASE 8 — SHADOW OUTCOME vs ML LABELS")

r = q("""
    WITH deduped AS (
        SELECT DISTINCT ON (symbol, DATE(created_at))
            id, symbol, pnl_pct, outcome, created_at
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    )
    SELECT
        outcome,
        COUNT(*) AS n,
        SUM(CASE WHEN pnl_pct > 0.168 THEN 1 ELSE 0 END) AS label_1_win,
        SUM(CASE WHEN pnl_pct <= 0.168 THEN 1 ELSE 0 END) AS label_0_loss,
        AVG(pnl_pct) AS avg_pnl,
        MIN(pnl_pct) AS min_pnl,
        MAX(pnl_pct) AS max_pnl,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct) AS median_pnl
    FROM deduped
    GROUP BY outcome
""")
log.info("[label by outcome] %s", json.dumps(r, default=str))

# tp with label=0 (anomaly)
r = q("""
    WITH deduped AS (
        SELECT DISTINCT ON (symbol, DATE(created_at))
            id, symbol, pnl_pct, outcome, created_at
        FROM decisions_log
        WHERE l3_pass=true AND decision='ALLOW'
          AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
          AND created_at >= NOW() - INTERVAL '90 days'
          AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ORDER BY symbol, DATE(created_at), created_at ASC
    )
    SELECT id, symbol, pnl_pct, outcome, created_at
    FROM deduped
    WHERE outcome='tp' AND pnl_pct <= 0.168
    ORDER BY pnl_pct ASC
    LIMIT 20
""")
log.info("[tp trades labeled as LOSS (pnl<=0.168)] %s", json.dumps(r, default=str))

# ─── FASE 9: BLOCK records — pnl_pct status ──────────────────────────────────
sep("FASE 9 — BLOCK RECORDS PNL STATUS")

r = q("""
    SELECT
        decision,
        COUNT(*) AS total,
        SUM(CASE WHEN pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS has_pnl,
        SUM(CASE WHEN pnl_pct IS NULL THEN 1 ELSE 0 END) AS no_pnl,
        SUM(CASE WHEN outcome IN ('tp','sl') THEN 1 ELSE 0 END) AS tp_sl_outcome,
        SUM(CASE WHEN outcome='timeout' THEN 1 ELSE 0 END) AS timeout_outcome,
        SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END) AS null_outcome
    FROM decisions_log
    WHERE l3_pass=true AND created_at >= NOW() - INTERVAL '90 days'
    GROUP BY decision
""")
log.info("[decisions by decision type with pnl/outcome breakdown] %s", json.dumps(r, default=str))

# shadow_trades with source=L3_REJECTED and their decisions_log pnl status
r = q("""
    SELECT
        st.source,
        COUNT(st.id) AS shadow_count,
        SUM(CASE WHEN dl.pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS dl_has_pnl,
        SUM(CASE WHEN dl.pnl_pct IS NULL THEN 1 ELSE 0 END) AS dl_no_pnl,
        SUM(CASE WHEN dl.id IS NULL THEN 1 ELSE 0 END) AS dl_missing,
        AVG(st.pnl_pct) AS avg_shadow_pnl
    FROM shadow_trades st
    LEFT JOIN decisions_log dl ON dl.id = st.decision_id
    WHERE st.created_at >= NOW() - INTERVAL '90 days'
    GROUP BY st.source
""")
log.info("[shadow by source vs decisions_log pnl] %s", json.dumps(r, default=str))

# ─── FASE 10: FUNIL COMPLETO ─────────────────────────────────────────────────
sep("FASE 10 — FUNIL COMPLETO SHADOW → TRAIN/VAL/TEST")

r = q("""
    SELECT
        (SELECT COUNT(*) FROM shadow_trades WHERE pnl_pct IS NOT NULL) AS s1_shadow_with_pnl,
        (SELECT COUNT(*) FROM shadow_trades st JOIN decisions_log dl ON dl.id=st.decision_id
         WHERE st.pnl_pct IS NOT NULL) AS s2_shadow_matched_dl,
        (SELECT COUNT(*) FROM decisions_log
         WHERE l3_pass=true AND decision='ALLOW' AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
        ) AS s3_dl_all_time,
        (SELECT COUNT(*) FROM decisions_log
         WHERE l3_pass=true AND decision IN ('ALLOW','BLOCK') AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
           AND created_at >= NOW() - INTERVAL '90 days'
        ) AS s4_dl_90d_all_decisions,
        (SELECT COUNT(*) FROM decisions_log
         WHERE l3_pass=true AND decision='ALLOW' AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
           AND created_at >= NOW() - INTERVAL '90 days'
        ) AS s5_dl_90d_allow_only,
        (SELECT COUNT(*) FROM decisions_log
         WHERE l3_pass=true AND decision='ALLOW' AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
           AND created_at >= NOW() - INTERVAL '90 days'
           AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
        ) AS s6_dl_90d_allow_excl_may,
        (SELECT COUNT(*) FROM (
            SELECT DISTINCT ON (symbol, DATE(created_at)) id
            FROM decisions_log
            WHERE l3_pass=true AND decision='ALLOW' AND outcome IN ('tp','sl') AND pnl_pct IS NOT NULL
              AND created_at >= NOW() - INTERVAL '90 days'
              AND NOT (created_at >= '2026-05-01' AND created_at <= '2026-05-20 23:59:59')
            ORDER BY symbol, DATE(created_at), created_at ASC
        ) d) AS s7_after_dedup
""")
log.info("[COMPLETE FUNNEL] %s", json.dumps(r[0], default=str))

# BLOCK pnl via shadow_trades (the actual EV data for rejected)
r = q("""
    SELECT
        COUNT(DISTINCT st.id) AS rejected_shadows,
        SUM(CASE WHEN st.pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS rejected_with_pnl,
        AVG(st.pnl_pct) AS avg_pnl,
        SUM(CASE WHEN st.outcome='TP_HIT' THEN 1 ELSE 0 END) AS tp_hit,
        SUM(CASE WHEN st.outcome='SL_HIT' THEN 1 ELSE 0 END) AS sl_hit
    FROM shadow_trades st
    WHERE st.source='L3_REJECTED'
      AND st.created_at >= NOW() - INTERVAL '90 days'
""")
log.info("[L3_REJECTED shadow stats] %s", json.dumps(r[0], default=str))

log.info("=== SQL AUDIT COMPLETE ===")
