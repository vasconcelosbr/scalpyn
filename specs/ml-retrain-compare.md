# ML Retrain & Comparação Pós-Mudanças Estruturais

## Objetivo
Disparar um novo ciclo de treino do modelo ML imediatamente após mudanças estruturais (indicadores técnicos, features, lógica de score) e exibir um relatório de comparação automático entre a nova versão e a versão ativa anterior na tela ML Models.

## Contexto
Após 4 sprints de correções estruturais (RSI/ADX Wilder's EMA, fix RSI invertido, fix EMA9<EMA50, threshold calibration, VWAP reset, Bollinger Bands ddof=0, unificação de serialização, etc.), o modelo ativo v2 foi treinado com dados e cálculos anteriores às correções. O sistema não retreina automaticamente após deploys — o cron semanal (domingo 3h UTC) não cobre a necessidade imediata de validar o impacto das mudanças no modelo.

## Requisitos

- **DEVE** disparar um novo ciclo de treino via `ml_trainer.job` imediatamente (trigger manual via endpoint ou Railway deploy)
- **DEVE** ao final do treino, gravar a nova versão em `ml_models` com todas as métricas: F1, AUC, Precision, Recall, Threshold, Capture, FPR, EV
- **DEVE** calcular e persistir um bloco de comparação `comparison_vs_previous` na nova versão contendo:
  - delta de cada métrica (nova − anterior)
  - flag `improved: true/false` por métrica (nova > anterior)
  - flag global `all_metrics_improved: true/false`
- **DEVE** exibir o bloco de comparação na tela ML Models ao expandir a nova versão, com indicadores visuais (↑ verde / ↓ vermelho) por métrica
- **DEVE** ativar a nova versão como modelo ativo independentemente do resultado da comparação (manter nova mesmo se pior)
- **PODE** exibir um badge "REGRESSÃO" na nova versão quando `all_metrics_improved = false`

## Restrições

- Stack: Railway cron service `scalpyn-ml-trainer` (Python, joblib, XGBoost, Optuna)
- Modelo armazenado em `ml_models.model_blob BYTEA` (PostgreSQL) — sem GCS
- Frontend: tela `/ml-models` já existente deve receber o dado de comparação via API existente
- O trigger imediato pode ser um deploy manual no Railway UI ou um endpoint `POST /api/ml/train`
- Não criar nova infraestrutura — usar o trainer e o banco já existentes

## Casos extremos

- **Treino falha (erro de dados, OOM, timeout):** nova versão não é gravada; v2 permanece ativo; erro visível nos logs do Railway
- **Dataset insuficiente (< N amostras):** treino abortado com mensagem clara; v2 permanece ativo
- **Nenhuma versão anterior existe:** `comparison_vs_previous = null`; nenhuma comparação exibida; nova versão ativada normalmente
- **Métricas da nova versão todas piores:** nova versão é ativada mesmo assim; badge "REGRESSÃO" exibido na UI

## Fora do escopo

- Rollback automático para v2 se nova versão for pior
- Notificação por e-mail ou Slack
- Re-treino automático disparado por CI/CD (webhook de deploy)
- Mudança na frequência do cron semanal

## Definição de concluído

- [ ] Trigger manual dispara novo ciclo de treino e grava nova versão (v3+) em `ml_models`
- [ ] Nova versão contém bloco `comparison_vs_previous` com deltas e flags por métrica
- [ ] Flag `all_metrics_improved` é calculada corretamente (true somente se F1, AUC, Precision, Recall e EV todos maiores que v2)
- [ ] Tela ML Models exibe a comparação ao expandir a nova versão com indicadores visuais ↑/↓
- [ ] Nova versão é marcada como ACTIVE independentemente do resultado
- [ ] Badge "REGRESSÃO" aparece quando alguma métrica regride
- [ ] Se treino falhar, v2 permanece ativo e erro é visível nos logs
