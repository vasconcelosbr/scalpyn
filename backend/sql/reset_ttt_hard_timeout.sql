-- Reset shadow_trades marcados como HARD_TIMEOUT pelo ttt_analyzer
-- para reprocessamento com fallback 5m.
--
-- Contexto: o ttt_analyzer consultava apenas timeframe='1m', que não existe
-- no banco (apenas '5m' e '30m'). Todos os trades foram marcados HARD_TIMEOUT
-- por ausência de dados. O fix em ttt_analyzer.py adiciona fallback para '5m'.
-- Este script reseta os registros para que o próximo ciclo do analyzer os
-- reprocesse com os candles 5m disponíveis.
--
-- Seguro executar múltiplas vezes (idempotente): só afeta ttt_close_reason='HARD_TIMEOUT'.
UPDATE shadow_trades
SET
    ttt_analysis_done      = FALSE,
    ttt_outcome            = NULL,
    ttt_close_reason       = NULL,
    ttt_fast_win_bucket    = NULL,
    time_to_tp_minutes     = NULL,
    max_profit_first_15m   = NULL,
    max_profit_first_30m   = NULL,
    max_profit_first_60m   = NULL,
    candles_to_peak        = NULL,
    candles_to_first_positive = NULL
WHERE ttt_close_reason = 'HARD_TIMEOUT'
  AND ttt_analysis_done = TRUE;

-- Confirma quantos registros foram resetados:
SELECT COUNT(*) AS resetados
FROM shadow_trades
WHERE ttt_analysis_done = FALSE
  AND ttt_enabled = TRUE;
