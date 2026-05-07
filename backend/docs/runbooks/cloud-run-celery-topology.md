# Cloud Run Celery Topology — Runbook

**Owner:** Plataforma · **Última revisão:** 2026-05-07 (Task #239)

## TL;DR

Produção precisa de **5 serviços Cloud Run** em `us-central1`. Se faltar **qualquer um** deles, o pipeline fica silenciosamente parado: o `scalpyn` (API) continua respondendo HTTP normalmente, mas nenhum candle é coletado, nenhum indicador é computado e nenhuma decisão de execução é avaliada.

```
scalpyn                     ← API (FastAPI), WORKER_QUEUES="", RUN_BEAT=0
scalpyn-worker-micro        ← consome `microstructure` (collect_5m, compute_5m)
scalpyn-worker-structural   ← consome `structural` (collect_all, pipeline_scan, …)
scalpyn-worker-execution    ← consome `execution` (evaluate, execute_buy, anti_liq)
scalpyn-beat                ← único agendador (RUN_BEAT=1, sem worker)
```

Definição canônica em [`cloudbuild.yaml`](../../../cloudbuild.yaml) (Task #216).

## Comando de verificação

```bash
gcloud run services list \
  --region=us-central1 \
  --format="value(metadata.name)" \
  --filter="metadata.name=(scalpyn OR scalpyn-worker-micro OR scalpyn-worker-structural OR scalpyn-worker-execution OR scalpyn-beat)"
```

Saída esperada: **5 linhas**, uma por serviço acima. Qualquer outro número é incidente.

O mesmo check roda como último step do `cloudbuild.yaml` (`topology-check`); builds futuros que percam um serviço falham vermelho na hora.

## Sintomas de topologia incompleta

| Faltando | Sintoma observável |
|---|---|
| `scalpyn-beat` | `MAX(time)` em `ohlcv` (qualquer timeframe) congela; `decision_log` para de receber linhas; `/api/system/celery-diagnostics` mostra `last_collect_all_start` antigo. |
| `scalpyn-worker-micro` | `MAX(time)` em `ohlcv WHERE timeframe='5m'` congela mesmo com beat vivo; `microstructure` enche em `/api/system/celery-status`. |
| `scalpyn-worker-structural` | `pipeline_scan`/`compute_indicators`/`collect_all` ficam pendurados em `structural`; indicadores 1h param. |
| `scalpyn-worker-execution` | `trade_tracking` zera (`pending=0` por horas); `evaluate_signals` enfileirado mas não consumido. |
| `scalpyn` (API) | 404/5xx no front; usuário percebe imediatamente. |

## Recuperação

1. Confirmar quais serviços existem com o comando acima.
2. Re-rodar o pipeline completo:

   ```bash
   gcloud builds submit --config=cloudbuild.yaml \
     --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)
   ```

   O Cloud Build é idempotente: serviços já corretos só recebem nova revisão; faltantes são criados. O step final `topology-check` valida o resultado.

3. Validar que os 5 estão `Serving` no console e que `MAX(time) FROM ohlcv WHERE timeframe='5m' AND symbol='BTC_USDT'` avança em até 10 minutos.

## Não fazer

- **Não deletar** nenhum dos 5 manualmente. Se um serviço precisa pausar, escalar `--min-instances=0 --max-instances=0` em vez de deletar — assim o `topology-check` não quebra builds futuros.
- **Não mover** `RUN_BEAT=1` para outro serviço (`scalpyn-beat` é o único agendador por design — Task #216, evita double-fire).
- **Não fundir** workers em um só (`WORKER_QUEUES=microstructure,structural,execution` num único serviço viola o isolamento de latência da fila `execution`).
