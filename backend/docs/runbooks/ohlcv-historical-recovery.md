# Recuperação histórica e continuidade das operações Shadow

Este PR entrega coleta contínua para dependências Shadow e recuperação de preços
fechados por manifesto. Não executa recuperação em produção, não recertifica
rótulos, não treina modelos e não altera gates de entrada/saída.

## Comportamento

- Os coletores canônicos usam a união dos símbolos spot ativos do pool com
  `shadow_trades.status IN ('PENDING','RUNNING')`, de todos os usuários e fontes.
  A resolução é refeita a cada rodada, sem cache. Normalização remove duplicatas;
  logs e resumo da rodada incluem `active_pool` e/ou `open_shadow`.
- Remover um ativo do pool continua excluindo novas entradas. A coleta adicional
  dura até a última dependência Shadow terminar. Não há alteração de pool,
  `is_tradable`, perfis, decisões ou operações. Shadow é spot-only no modelo atual.
- O resolvedor spot do WebSocket preserva essas dependências no refresh/reconnect,
  mantendo a seleção de pool/fallback existente. Não muda subscriptions futures.
  O encerramento libera a dependência no próximo refresh; REST resolve a cada rodada.
- O recuperador só aceita `gate.io/spot` e os timeframes canônicos de estado,
  com intervalo UTC alinhado `[start,end)`. Contrato e finalização são congelados
  no manifesto e conferidos novamente em cada página. Intervalos anteriores à
  vigência do contrato são rejeitados, não reatribuídos ao contrato novo.
- Cada chamada usa `from/to` explícitos. O cursor é a primeira lacuna do intervalo
  completo, nunca `max(time)`. Respostas parciais, vazias, abertas, inválidas ou
  rate-limited ficam visíveis; esgotar tentativas não significa completude.
- Inserção usa `ON CONFLICT DO NOTHING` e `ingested_at=clock_timestamp()`.
  Preços e chegadas existentes são imutáveis. Nenhuma escrita em `ohlcv_live`,
  indicadores, trades, decisões ou labels. Dados tardios continuam tardios nos
  controles temporais; recuperar preços não recupera automaticamente fluxo/taker.
- Manifesto, autor, razão, hash, cursor e recibos são persistidos. Cada recibo
  contém intervalo solicitado, configuração aplicada, chegada da resposta, hash
  dos preços normalizados, inserções e lacunas restantes. Não armazena credenciais.
- Uma transação por página inclui preços, cursor e recibo. Falha/kill antes do
  commit reverte todos; novo envio retoma a partir do banco. Não há retry Celery
  automático nem agendamento periódico. Manifestos idênticos retornam o mesmo job.

## Configuração e autorização

A migração `246_ohlcv_recovery`, descendente de
`245_ohlcv_comparison_cursor`, cria somente tabelas operacionais novas:
`ohlcv_recovery_owner`, `ohlcv_recovery_jobs`, `ohlcv_recovery_attempts`.
Não modifica hypertables, não registra um operador e não habilita tarefas.
Downgrade preserva os registros de auditoria.

Após revisão/merge, um rollout separado deve aplicar a migração, verificar o
schema e provisionar explicitamente o singleton de operador (`user_id` existente
e `authorization_reason`). Só esse usuário pode ler/salvar a configuração global
`config_profiles.config_type='ohlcv_recovery'`, via `/settings/ohlcv` ou API de
configuração autenticada. Sem configuração completa, a operação falha fechada.

Todos os campos são obrigatórios, sem defaults operacionais:

| Campo | Semântica |
| --- | --- |
| `enabled` | Habilita submissão/execução; preview continua disponível |
| `max_manifest_candles` | Limita o intervalo total, inclusive candles existentes |
| `page_candles` | Limita cada request ao máximo de protocolo Gate de 1000 candles |
| `max_pages_per_run` | Limita páginas de uma entrega Celery |
| `max_attempts_per_gap` | Limita respostas sem avanço da primeira lacuna |
| `request_spacing_seconds` | Espaçamento mínimo entre requisições de recuperação |
| `retry_delay_seconds` | Cooldown de falha/lacuna sem avanço |
| `http_timeout_seconds` | Timeout HTTP da recuperação |
| `statement_timeout_ms`, `lock_timeout_ms` | Limites SQL por transação |
| `dispatch_ttl_seconds` | TTL de deduplicação do envio |

Os inteiros são positivos. `page_candles` tem teto de protocolo, não de estratégia.
Escolher budgets com base no preflight antes da ativação; este PR não presume
capacidade de produção nem grava valores de exemplo. Validar tempo máximo da
rodada contra os limites do worker e dimensionar TTL de dispatch de acordo.

## Operação explícita

Executar a partir de `backend`, no ambiente autorizado. Credenciais permanecem
no secret store/ambiente; nunca dentro do manifesto ou no Git.

Criar arquivo JSON com os campos abaixo, preenchidos a partir da auditoria e do
contrato canônico real: `exchange`, `market_type`, `symbol`, `timeframe`, `start`,
`end`, `capture_contract_version`, `finalization_delay_seconds`, `reason`.
Cada manifesto cobre uma identidade e um intervalo; usar manifestos separados
para outros símbolos/timeframes. Datas exigem timezone explícita.

```text
python -m scripts.ohlcv_recovery --owner <UUID> plan --manifest <arquivo.json>
python -m scripts.ohlcv_recovery --owner <UUID> submit --manifest <arquivo.json> --sha256 <hash-revisado>
python -m scripts.ohlcv_recovery --owner <UUID> enqueue --job <UUID-do-job>
python -m scripts.ohlcv_recovery --owner <UUID> status --job <UUID-do-job>
```

`plan` é somente leitura: retorna hash normalizado, lacunas exatas e
`source_recoverability=UNKNOWN_UNTIL_REQUESTED`. Revisar intervalo, razão, custo
máximo e hash antes de `submit`; qualquer alteração exige nova revisão do hash.
`submit` registra o manifesto, mas não publica trabalho. `enqueue` passa pelo
dispatcher central com dedup e roteamento `research_ohlcv`. O worker canônico
continua carregando exclusivamente os coletores canônicos.

O lock do singleton serializa jobs históricos e seu cooldown persiste entre
processos/restarts. `Retry-After` é respeitado também entre jobs diferentes.
Esse orçamento limita a recuperação; **não é um rate limiter global de todos
os clientes Gate existentes**. Antes de ativar, reservar folga para os coletores
e acompanhar 429/latência. Não misturar filas no worker canônico.

Reenviar um job `PARTIAL` após `next_attempt_at` retoma o cursor persistido.
`WAIT`/`BUSY` não fazem HTTP nem alteram preços. A entrega termina quando atinge
o limite de páginas, uma lacuna parcial ou um erro; não ocupa a fila com retries
infinitos. `EXHAUSTED` exige investigação da fonte/identidade e revisão explícita
de um novo manifesto com razão documentada; reenviar o mesmo hash não zera tentativas.
Um conflito com candle existente inválido permanece lacuna, pois o recuperador
não sobrescreve dados existentes. Correção desses registros exige outro procedimento.

`status` reconsulta a cobertura real, em vez de inferi-la de `status='COMPLETE'`.
Os recibos podem ser consultados por `job_id`, ordenados por `id`; seu campo
`remaining_page_gaps` mostra o que a exchange não entregou na página.

## Validação de rollout futuro

1. Confirmar migração aplicada e nenhum job/config habilitado por efeito do deploy.
2. Confirmar worker canônico isolado e worker research consumindo a rota nova.
3. Conferir união pool/Shadow e logs `inclusion_reasons`, incluindo um símbolo
   já fora do pool. Verificar REST persistido e assinatura WS após refresh.
4. Revisar o primeiro manifesto em dry-run, com limite conservador aprovado.
5. Após execução autorizada, comparar lacunas do preview/status e recibos com
   `ohlcv`, confrontando contagem e timestamps. Cobertura exige intervalos
   contíguos, não apenas último timestamp. Inspecionar `ingested_at` real.
6. Confirmar chegada dos candles correntes durante recuperação e ausência de
   crescimento anormal da fila canônica, erros SQL, 429 e pressão de disco.
7. Verificar que nenhuma decisão, operação ou certificação foi reescrita.

Rollback operacional: salvar `enabled=false` e parar novos envios. A página
em andamento pode concluir; a próxima revalida a configuração. Não apagar
preços, jobs ou recibos. Para interrupção imediata, parar somente o worker de
recuperação/research conforme o procedimento do ambiente, preservando o canônico.

Ficam fora deste PR o reconciliador periódico automático, recertificação de
rótulos e reconstrução de features point-in-time. A recuperação histórica é
deliberadamente dirigida por manifestos revisados, sem ampliar escopo sozinha.

## Validação automatizada

`tests/test_ohlcv_recovery_postgres.py` aplica a migração real em schema isolado
de PostgreSQL local e usa respostas HTTP determinísticas para paginação, retomada,
idempotência, chegada tardia, resposta parcial/vazia/aberta/fora de escopo, 429,
concorrência, cooldown, orçamento, autorização, dry-run read-only e rollback.
Também valida remoção do pool, várias dependências/usuários/fontes, normalização,
separação spot/futures e resolução WebSocket.

O workflow OHLCV mantém os critérios PR-A e executa o teste Redis/Celery com
processos separados tanto para comparação quanto para recuperação histórica.
Os testes locais exigem `PR_A_TEST_DATABASE_URL` apontando para
`localhost/127.0.0.1` e banco `scalpyn_pr_a_test`, e `PR_A_TEST_REDIS_URL` para
Redis local no DB de teste. Nenhuma chamada à exchange/produção ocorre nos testes.
