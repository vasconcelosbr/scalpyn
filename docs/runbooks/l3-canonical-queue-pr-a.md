# PR-A: coleta canônica isolada e comparação incremental

## Escopo

Este PR prepara código e validação. Não provisiona serviços, não aplica migrações
em produção, não muda perfis de produção, não recupera candles e não treina modelos.
As correções de universo de coleta e recuperação de lacunas pertencem aos PRs seguintes.

`collect_1m_shadow`, `collect_5m_shadow` e `collect_30m_shadow` passam para
`canonical_ohlcv`. Os nomes históricos das tarefas são preservados. Comparação,
pesquisa, retenção, sampler e backfills permanecem em `research_ohlcv`.
Cadências, expiração das mensagens, concorrência por símbolo e contrato de
finalização não são relaxados. O worker canônico exige fila exclusiva e
`CELERY_CONCURRENCY` explicitamente configurada. O prefetch existente é preservado.

## Comparação e configuração

A comparação periódica usa janelas semiabertas `[window_start, window_end)`.
Cada rodada visita os timeframes do contrato; `max_rounds_per_run` limita rodadas,
não o total de janelas. A transação grava resultado e próximo início juntos.
Um advisory lock transacional impede execuções concorrentes do mesmo cursor.
Exceder o orçamento de registros aborta a janela inteira, sem certificar uma
amostra truncada. Timeouts ou queda antes do commit também não avançam o cursor.

Os limites obrigatórios ficam em `config_profiles`, `config_type=ohlcv_comparison`,
sem pool. A tabela singleton `ohlcv_comparison_owner` define o operador autorizado;
a migração não atribui operador nem semeia configuração. Provisionamento de owner
é um passo administrativo futuro, com `user_id` e `authorization_reason` revisados.
Ausência de configuração gera `operator_config_missing`; desabilitação gera
`operator_config_disabled`. A coleta continua independente dessa comparação.

O operador usa `/settings/ohlcv` e a API de configuração existente. GET/PUT são
restritos ao owner; PUT valida o schema e usa `ConfigAuditLog` existente.

| Campo | Significado |
| --- | --- |
| `enabled` | Habilita a comparação periódica |
| `window_seconds` | Extensão temporal máxima de cada consulta |
| `max_rounds_per_run` | Máximo de rodadas por invocação |
| `max_shadow_rows` | Orçamento de candles shadow por janela |
| `statement_timeout_ms` | Limite de cada instrução na transação de comparação |
| `lock_timeout_ms` | Espera máxima por locks de banco |
| `settlement_delay_seconds` | Distância mínima entre o corte da comparação e agora |

Os limites precisam ser dimensionados e registrados antes de ativar. Não há
valores implícitos no runtime. O resultado corresponde ao estado observado na
janela, não é certificação de completude nem de elegibilidade de ML. Chegadas
tardias não reescrevem snapshots anteriores. Elas exigem comparação explícita
do intervalo; a recuperação e recertificação ficam fora deste PR.

Resultados incrementais ficam em `ohlcv_comparison_windows`; snapshots históricos
completos continuam preservados em sua tabela anterior. Não somar percentis ou
misturar esses formatos. Os novos contadores referem-se à população shadow:
exatos, divergentes e sem correspondente canônico. Não representam o universo
inteiro de candles canônicos.

Recomparação histórica explícita, somente leitura, sem alterar cursor:

```text
python -m scripts.compare_ohlcv_range --start <ISO-com-timezone> --end <ISO-com-timezone> --timeframe <timeframe-do-contrato>
```

O comando usa os mesmos limites por janela e exige um intervalo explícito.

## Instrumentação

`[OHLCV-TASK]` registra tentativa de publicação, publicação confirmada, início,
fim e descarte. `expired` vem do sinal Celery, separado de revogação manual.
`queue_wait_seconds` mede tentativa de publicação até início, incluindo envio
ao broker. `scheduled_eta` fica nulo quando o produtor não forneceu ETA: não
representa atraso do relógio do beat. Nenhum argumento ou credencial é registrado.

`[OHLCV-COLLECTION]` registra resposta observada, persistência concluída e
intervalo entre esses eventos por símbolo/timeframe. Esse intervalo inclui
processamento e pacing; não é uma medida exclusiva do PostgreSQL.

`/api/system/celery-status` mostra consumidores por fila. `consumer_missing=true`
indica nenhum consumidor observado; `null` indica inspeção inconclusiva. A mera
existência de outro worker ou fila vazia não prova cobertura da fila canônica.
Telemetria de logs depende da retenção operacional existente; o PR de recuperação
histórica deverá definir a preservação de evidências vinculadas a datasets.

## Publicação futura — não executada por este PR

- Confirmar merge, SHA canônico, auditoria de schema, configuração planejada,
  orçamento de recursos e janela de observação antes de qualquer deploy.
- Aplicar a migração aditiva pelo procedimento autorizado. Ela cria somente
  tabelas operacionais novas; não altera hypertables, preços ou contratos.
- Disponibilizar o consumidor dedicado com o novo código, `WORKER_QUEUES=canonical_ohlcv`,
  `RUN_BEAT=0`, `SERVICE_MODE=worker` e concorrência explicitamente dimensionada.
  Verificar a fila anunciada pelo worker antes de mudar beat/produtores.
- Atualizar produtores/beat e o worker de pesquisa. Mensagens antigas da fila
  de pesquisa podem terminar com o código antigo; monitorar sua drenagem, sem purge.
- Confirmar timestamps de candles efetivamente persistidos, distribuição dos
  atrasos e ausência de pesquisa/backfill no consumidor canônico. Comparar com o
  contrato vigente, registrando intervalo, população e impedimentos observados.
- Provisionar o operador e salvar a configuração revisada somente quando a
  comparação incremental for desejada. Confirmar checkpoint e resultados no banco.

Critério de aceite operacional: coleta continua sob carga de pesquisa; ausência
de consumidor é visível; atraso atende ao contrato vigente durante a janela
pré-registrada; reinício retoma o cursor sem duplicar janelas confirmadas.
Testes locais não constituem prova desses resultados em produção.

## Reversão futura

Desabilitar comparação pela configuração auditada antes de reverter código.
Coordenar produtores e consumidores: manter o consumidor canônico até drenar
suas mensagens, depois restaurar o roteamento anterior se necessário. Não purgar
filas, apagar cursores ou remover evidências. O downgrade da migração preserva
deliberadamente essas tabelas; a versão anterior do aplicativo não depende delas.
O roteamento anterior reintroduz o risco original e deve ser registrado como tal.

## Validação reproduzível

Os testes PostgreSQL exigem `PR_A_TEST_DATABASE_URL` apontando para um banco
local chamado `scalpyn_pr_a_test`. Criam um schema exclusivo, aplicam a nova
migração sobre as dependências mínimas e removem apenas esse schema ao terminar.
Isso valida a nova migração em PostgreSQL real; não equivale a executar toda a
cadeia histórica com TimescaleDB desde banco vazio.

O teste Redis exige `PR_A_TEST_REDIS_URL` em loopback e database `/15`, com filas
exclusivas. Executa workers Celery reais com corpos de tarefa sintéticos, sem
chamar exchange, dados de produção ou execução de ordens. Comprova consumo
independente enquanto a pesquisa permanece bloqueada e descarte de mensagem expirada.

No diretório `backend`:

```text
python -m pytest -q tests/test_ohlcv_queue_isolation.py tests/test_ohlcv_comparison_postgres.py tests/test_ohlcv_broker_isolation.py tests/test_research_ohlcv_pipeline.py tests/test_celery_routing_invariants.py tests/test_celery_queue_status_and_dedup.py tests/test_celery_diagnostics_endpoint.py
python -m alembic heads
python -m alembic history
```

Sem as variáveis de integração, pytest marca esses testes como skipped. Isso
nunca deve ser reportado como integração validada.

No diretório `frontend`:

```text
npx tsc --noEmit
npx eslint app/settings/ohlcv/page.tsx components/layout/navItems.ts
```

A branch de revisão está desabilitada para deploy automático no `frontend/vercel.json`,
conforme a [configuração Git da Vercel](https://vercel.com/docs/project-configuration/git-configuration#git.deploymentenabled).
Essa regra não desabilita deploys de outras branches nem altera configuração remota.

## Evidência desta implementação

Suite focada executada com PostgreSQL e Redis locais reais, sem skips:

```text
92 passed in 15.25s
```

`tsc --noEmit`, lint dos arquivos frontend alterados e verificação de sintaxe do
script de startup concluíram sem erros. O grafo Alembic apresentou head único.
A auditoria remota de schema foi somente leitura e retornou `OK — no drift detected`.
Nenhum teste de carga ou aceite operacional foi executado em produção.

| Número reportado | Origem | Valor literal |
| --- | --- | --- |
| Testes da suite focada e duração | [command] pytest, comando acima | `92 passed in 15.25s` |
