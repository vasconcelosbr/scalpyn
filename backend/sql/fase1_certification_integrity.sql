-- Fase 1 — Bloco C: query de certificação de integridade (read-only).
-- Parâmetros: :W_FROM / :W_TO (janela avaliada), :VALID_FROM (chave
-- ml_dataset_valid_from da config ml ativa), :PISO_D3 (piso de geração, D3=80).
-- COL_PNL (DECISÃO A-1): shadow_trades não tem net_pnl_pct → pnl_pct.
-- População canônica: source='L1_SPECTRUM' AND barrier_mode='ATR_DYNAMIC'.
-- Nota vinculante I09: em janelas pré-deploy/históricas é INFORMATIVO;
-- na execução do job (Bloco D) é FAIL.

WITH pop AS (
  SELECT * FROM shadow_trades
  WHERE source = 'L1_SPECTRUM'
    AND barrier_mode = 'ATR_DYNAMIC'
    AND entry_timestamp >= :W_FROM
    AND entry_timestamp <  :W_TO
)
SELECT 'I01_outcome_casing' AS invariante,
       COUNT(*) AS violacoes,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status
FROM pop WHERE outcome IS NOT NULL AND outcome <> UPPER(outcome)
UNION ALL
SELECT 'I02_contratos_nulos_em_elegiveis', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE eligible_for_training IS TRUE
  AND (feature_schema_version IS NULL OR label_contract_version IS NULL
       OR barrier_contract_version IS NULL OR capture_contract_version IS NULL)
UNION ALL
SELECT 'I03_elegivel_pre_valid_from', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM shadow_trades
WHERE eligible_for_training IS TRUE AND entry_timestamp < :VALID_FROM
UNION ALL
SELECT 'I04_snapshot_incompleto', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE config_snapshot->>'barrier_mode' IS NULL
   OR config_snapshot->>'atr_multiplier_sl' IS NULL
   OR config_snapshot->>'win_fast_threshold_seconds' IS NULL
UNION ALL
SELECT 'I05_flag_x_lineage_divergente', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE (eligible_for_training IS TRUE AND lineage_status IS DISTINCT FROM 'EXACT')
   OR (eligible_for_training IS FALSE AND lineage_status = 'EXACT')
UNION ALL
SELECT 'I06_coverage_baixa_em_elegiveis', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE eligible_for_training IS TRUE AND (features_coverage IS NULL OR features_coverage < 0.8)
UNION ALL
SELECT 'I07_tp_hit_pnl_negativo', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE outcome = 'TP_HIT' AND pnl_pct < 0
UNION ALL
SELECT 'I08_atr_nulo_em_completed_acima_de_meio_pct',
       COUNT(*) FILTER (WHERE atr_pct_at_entry IS NULL),
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            WHEN COUNT(*) FILTER (WHERE atr_pct_at_entry IS NULL)::numeric / COUNT(*) <= 0.005
            THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE status = 'COMPLETED'
UNION ALL
SELECT 'I09_geracao_abaixo_do_piso',
       COUNT(*),
       CASE WHEN COUNT(*) >= :PISO_D3 THEN 'PASS' ELSE 'FAIL' END
FROM pop
UNION ALL
SELECT 'I10_duplicidade_elegivel', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM (SELECT symbol, entry_timestamp FROM pop
      WHERE eligible_for_training IS TRUE
      GROUP BY 1, 2 HAVING COUNT(*) > 1) d
UNION ALL
SELECT 'I11_holding_negativo', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM pop WHERE holding_seconds < 0
UNION ALL
-- Fase 1.4 (P1 ação A): cobertura L3/L3_LAB via colunas dedicadas (o que o
-- treino lê), não via config_snapshot. Invariante dedicado (não estende I04).
SELECT 'I12_l3_economic_contract', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM shadow_trades
WHERE source IN ('L3', 'L3_LAB')
  AND eligible_for_training IS TRUE
  AND entry_timestamp >= :W_FROM AND entry_timestamp < :W_TO
  AND (barrier_mode IS NULL OR tp_pct_applied IS NULL
       OR sl_pct_applied IS NULL OR barrier_contract_version IS NULL);

-- Query cumulativa complementar (informativa, sem PASS/FAIL):
-- elegíveis maturados pós-fronteira + projeção de dias para as metas de
-- readiness. Fase 1.3: metas config-driven (:MILESTONE_ROWS = milestone,
-- :RETRAIN_ROWS = gate de retrain); a meta estendida (5000) saiu do display.
WITH mediana AS (
  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY n) AS med
  FROM (SELECT date_trunc('day', entry_timestamp) d, COUNT(*) n
        FROM shadow_trades
        WHERE source='L1_SPECTRUM' AND barrier_mode='ATR_DYNAMIC'
          AND eligible_for_training IS TRUE AND outcome IS NOT NULL
          AND entry_timestamp >= :VALID_FROM
          AND entry_timestamp < date_trunc('day', now())
        GROUP BY 1 ORDER BY 1 DESC LIMIT 7) t
)
SELECT COUNT(*) AS elegiveis_maturados_pos_boundary,
       (SELECT med FROM mediana) AS mediana_diaria_7d,
       CEIL(:MILESTONE_ROWS::numeric / GREATEST(1, (SELECT med FROM mediana))) AS dias_para_milestone,
       CEIL(:RETRAIN_ROWS::numeric / GREATEST(1, (SELECT med FROM mediana))) AS dias_para_retrain,
       now() AS calculado_em
FROM shadow_trades
WHERE source='L1_SPECTRUM' AND barrier_mode='ATR_DYNAMIC'
  AND eligible_for_training IS TRUE AND outcome IS NOT NULL
  AND entry_timestamp >= :VALID_FROM;
