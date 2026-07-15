# Crypto EV Score - Implementacao e evidencias

Data: 2026-07-08

## Escopo

Implementacao do Crypto EV Score como score operacional por cripto, pos-sinal, calculado a partir de `shadow_trades` de `source='L1_SPECTRUM'`. O score nao participa de ML.

## Validacao previa do dataset

Consulta read-only em producao via Railway/Postgres:

```text
ml_dataset_valid_from = 2026-07-05 19:45:49+00:00
ml_fee_roundtrip_pct = 0.20
total_l1_spectrum_closed = 380
pnl_pct_null = 0
net_return_pct_null = 0
pnl_derivable_from_prices = 0
empty_features_snapshot = 0
first_created_at = 2026-07-05 19:50:19.205530+00:00
last_created_at = 2026-07-08 13:45:20.057478+00:00
```

Conclusao: o dataset atual tem PnL liquido disponivel para o calculo operacional. Nao foi necessario derivar PnL por preco.

## Decisao sobre L3 executable

A amostra atual de `features_snapshot` L1 contem campos de mercado como ATR, mas nao contem contexto auditavel de perfil/L3 (`profile_id`, `profile_passed`, `l3_passed` ou equivalente). Por isso, o replay L3 foi implementado fail-closed:

```text
would_pass_l3 = NULL
replay_status = UNREPLAYABLE
replay_reason = missing_l3_snapshot_context
```

Efeito: a view `EV_spectrum` calcula score por simbolo; a view `EV_executable` exclui trades `UNREPLAYABLE`, conta `n_excluded_unreplayable` e degrada para `INSUFFICIENT_DATA` quando o ratio configurado for excedido. Dado faltante nao e classificado como rejeicao L3.

## Guard de ML

Foram adicionadas barreiras executaveis contra vazamento operacional:

- prefixo `crypto_ev*` proibido em colunas de ML;
- prefixo `post_model_operational*` proibido em colunas de ML;
- snapshots que contenham esses campos fazem o build do dataset falhar fechado.

Teste executado:

```text
python -m pytest tests/test_crypto_ev_ml_leakage_guard.py -q
3 passed in 0.98s
```

## Componentes implementados

- Alembic `129_crypto_ev_score.py`
- tabelas `crypto_ev_l3_replay_flags` e `crypto_ev_snapshots`
- view `crypto_ev_current`
- config `config_profiles.config_type='crypto_ev'`
- task Celery `app.tasks.crypto_ev_score.compute`
- APIs `/api/crypto-ev/*`, `/api/config/crypto_ev`, `/api/config/crypto_ev/reset`
- health gate `/api/ml/models/health`
- enriquecimento da Watchlist com `crypto_ev`, incluindo `n_excluded_unreplayable`
- coluna EV na Watchlist e na tabela Futures
- pre-registro v2 em `docs/crypto_ev_preregistration_2026-07-08_v2.md`

## Status operacional

Migration criada, mas nao aplicada em producao neste passo. O score permanece observacional ate aplicacao controlada da migration, execucao da task e validacao G pre-registrada.
