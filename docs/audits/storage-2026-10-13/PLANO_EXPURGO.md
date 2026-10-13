# Plano de expurgo recorrente — Scalpyn

Status: execução autorizada pelo usuário em 2026-10-13: “ok executar o plano de expurgo”. Este documento registra o planejamento. A ampliação de capacidade permanece pendente.

## Evidência e limites

Inventário de produção: `2026-10-13 15:06:53.626037+00:00` [query]. Banco: `88594499263` bytes [query]. Fonte e SQL completos em [database-inventory.json](database-inventory.json). Tamanhos incluem dados, TOAST e índices; não representam bytes removíveis. Estatísticas `n_live_tup` e `n_dead_tup` são estimativas, não contagens exatas nem medição de bloat.

As consultas de elegibilidade foram subsequentes ao inventário, em um banco ativo. Não formam um snapshot transacional único com os tamanhos. As tentativas de contar a idade de toda a telemetria, de `indicators` e dos candidatos da fila com confirmação de label atingiram o timeout de leitura; quantidade e bytes efetivamente removíveis nesses grupos: **NÃO DISPONÍVEL**. Não serão estimados pelo tamanho total das tabelas. Fonte: [eligibility.json](eligibility.json).

Código inspecionado: `origin/main`, commit `0bcd63751d209d113d8b03cffb93720dd9abd714` [comando]. Os logs confirmam execução de rotinas existentes, mas esta análise não certifica todo o conteúdo de cada imagem em execução. A revisão de consumidores cobre o repositório; consumidores externos, notebooks e consultas ad hoc não foram certificados.

## Maiores tabelas

| Tabela | GB [calc] | Bytes literais [query] |
|---|---:|---:|
| `indicators` | 38.341 | `38341017600` |
| `pump_radar_indicator_values` | 5.504 | `5504327680` |
| `pump_opportunity_labels` | 5.052 | `5052342272` |
| `decisions_log` | 4.515 | `4515037184` |
| `shadow_l3_flow_trades` | 4.295 | `4295122944` |
| `ohlcv_state_ingestion_observations` | 3.947 | `3946520576` |
| `shadow_l3_exit_decisions` | 3.768 | `3768279040` |
| `shadow_trades` | 3.671 | `3671138304` |
| `ohlcv` | 3.267 | `3267411968` |
| `pump_opportunity_observations` | 2.921 | `2920669184` |
| `opportunity_snapshots` | 1.590 | `1589641216` |
| `pump_radar_ohlcv` | 1.403 | `1402740736` |
| `indicator_snapshots` | 1.287 | `1287168000` |
| `pump_opportunity_price_paths` | 1.184 | `1183531008` |
| `trade_simulations` | 0.919 | `919445504` |
| `pump_opportunity_label_queue` | 0.837 | `836943872` |
| `trade_decisions` | 0.699 | `699031552` |
| `l3_gate_v2_evaluations` | 0.506 | `505511936` |

GB decimal [calc]: `total_bytes / 1000000000`, arredondado apenas para exibição. A coluna de bytes é o ledger literal [query]. As partições de `pump_research_labels` somam `982876160` bytes e as de `pump_research_minute`, `970571776` bytes [query]; não somar novamente seus índices, já incluídos no total de cada partição.

## O que pode entrar no expurgo

| Grupo | Conclusão de uso | Regra proposta e condição de liberação |
|---|---|---|
| `ohlcv_state_ingestion_observations` | Telemetria de ingestão. No código analisado há escritores e verificações em migrations; não encontrei leitura direta pelos treinadores L3/Pump ou pelo histórico de Shadow. | Primeiro candidato. Consolidar agregados de saúde por dia/símbolo/timeframe/contrato, preservar incidentes marcados e últimas evidências de contratos, e remover apenas telemetria vencida. Janela inicial proposta abaixo; conferir consumidores externos antes de ativar. |
| `ohlcv_settlement_latency_samples` | Amostras de atraso de chegada, separadas das velas usadas em treino e gráfico. | Mesma política de telemetria, preservando séries/percentis necessários a estudos de latência e incidentes. Não apagar `ohlcv` nem estados canônicos. |
| `pump_opportunity_label_queue` | Controle de trabalho. Seleção de treino usa observações e `pump_opportunity_labels`; a fila também expõe bloqueios no detalhe da observação. | Excluir somente entradas concluídas antigas, sem `resource_block`, com label persistido de mesma chave composta. Manter pendentes, bloqueadas e finalizações sem label. Não apagar os rótulos junto com a fila. |
| `indicator_snapshots` | Já tem expurgo; alertas operacionais usam dados recentes. O registro de datasets de MARKET_REGIME também cita a tabela. | Manter a política existente, acrescentando proteção a datasets/evidências congelados. Não reduzir a janela só porque os alertas consultam um intervalo curto. |
| `shadow_l3_flow_trades` | Fluxo bruto usado na continuação/saída do Shadow; não é descartável enquanto houver candidato não finalizado ou trade gerenciado aberto. | Reutilizar o expurgo existente, com sua proteção por símbolo, exchange e intervalo de entrada/warmup. Manter envelopes `shadow_l3_exit_decisions` permanentemente. Validar replays/auditorias pendentes antes de remover fluxo vencido. |
| `pump_opportunity_observations`, `pump_opportunity_labels`, `pump_opportunity_price_paths` | Dataset do Pump de observações, formação de labels e universo do modelo de velas. | Acrescentar retenção hoje ausente, respeitando maior janela de pesquisa e necessidades pendentes. Arquivar datasets congelados e suas fontes antes de apagar evidência necessária à reprodução. Respeitar FKs e referências em manifests/JSON. Atualmente não há observações vencidas pela janela configurada. |
| `pump_radar_indicator_values`, `pump_radar_indicator_snapshots`, `pump_radar_ohlcv` | Radar histórico é um módulo distinto do Pump Monitor. Indicadores alimentam relatórios e comparações ligadas à identidade de Shadow. | Candidatos a arquivamento por execução concluída/abandonada, não a exclusão indiscriminada. Proteger eventos ligados ao Shadow e relatórios ainda acessíveis. Reconstituir uma execução arquivada antes de permitir exclusão do original. |
| `indicators` | Leituras atuais e reconstrução histórica da calibração MTF. É o maior alvo potencial, mas há dependência temporal real. | Não reduzir retenção agora. Congelar previamente os indicadores exatos usados por cada trade/dataset e adaptar a calibração ao snapshot congelado. Só depois medir quais linhas históricas deixam de ter consumidores. Preservar também o último valor por chave operacional. |

Os registros da fila são `2459184`, dos quais `2367833` concluídos, `91351` pendentes e `0` com bloqueio [query]. **Concluído não significa automaticamente elegível**: idade e existência do label ainda precisam ser confirmadas. As observações do Pump são `410225`, com `0` vencidas [query].

## Preservação obrigatória

Excluir do expurgo genérico: `shadow_trades`, `shadow_l3_exit_states`, `shadow_l3_exit_decisions`, auditorias de fechamento/preço, relatórios e itens de Shadow, `decisions_log` ligado aos trades, `trade_tracking`, `trade_simulations`, evidências L3, `opportunity_snapshots` ainda em avaliação, versões de profiles/configurações, datasets congelados, registry/modelos, artefatos, métricas e logs de treinamento.

`trade_simulations` ainda tem consumidor de dataset; `opportunity_snapshots` representa a população aprovada e rejeitada e possui avaliador de resultados futuros. Não são lixo por estarem fora da consulta principal do treinador atual. Decisões rejeitadas também podem compor a população contrafactual; não removê-las apenas pela ausência de um trade.

Preservar `ohlcv` nas janelas de todos os modelos, inclusive aquecimento, embargo, horizonte de label e avaliação ao vivo, e em cada intervalo necessário ao gráfico de Shadow. O endpoint de gráfico consulta `ohlcv` diretamente: apagar velas antigas pode manter a linha do trade e ainda destruir seu gráfico. Preservar `pump_flow_asof` e demais séries de pesquisa exigidas pelos experimentos pré-registrados.

## Configuração real encontrada

Extratos literais [config: DB]; são projeções dos campos, não a configuração completa:

```json
{
  "pump_opportunity": {
    "budget": {"max_storage_bytes": 10000000000, "retention_days": 30},
    "research": {"lookback_days": 30, "candle": {"lookback_days": 90}}
  },
  "shadow_l3_exit_policy": {"retention_days": 30, "capture_batch_size": 10000},
  "pump_monitor": {
    "flow": {"retention_days": 2},
    "snapshots": {"retention_hours": 48},
    "flow_history": {"retention_days": 180},
    "research": {"retention_days": 30}
  }
}
```

O expurgo de `indicators` já funciona: `[prune-indicators] deleted=3042 batches=1 retention_days=90 hit_cap=False` [log]. A rotina `pump_monitor.purge` também conclui, mas seu resultado cobre buckets, snapshots, capital, flow history e previsões ao vivo; não cobre o conjunto `pump_opportunity_*`. A manutenção de research OHLCV limpa `ohlcv_ingestion_observations`, não a tabela distinta `ohlcv_state_ingestion_observations`. Logs locais: [indicators](prune-indicators.log), [Pump](pump-purge.log).

## Implantação proposta

### Etapa de inventário e simulação

Criar um serviço de retenção com lista explícita de tabelas permitidas. Políticas em `config_profiles`, versionadas, editáveis na interface e com auditoria. Começar em simulação: retornar cutoff, candidatos, proteção por dependência, estimativa de carga e motivo de bloqueio, sem DELETE. As consultas precisam usar índices; onde a contagem já excedeu o timeout, validar EXPLAIN e ajustar o caminho de acesso antes de liberar escrita.

Configuração inicial proposta, **não instalada e não confundível com configuração vigente**:

```json
{
  "mode": "dry_run",
  "cadence_seconds": 3600,
  "batch_rows": 1000,
  "max_batches_per_run": 10,
  "statement_timeout_ms": 3000,
  "lock_timeout_ms": 500,
  "max_runtime_seconds": 60,
  "telemetry_retention_days": 7,
  "completed_label_queue_retention_days": 7,
  "preserve_model_and_shadow_evidence": true,
  "archive_required_for_research_datasets": true
}
```

Esses números são escolhas iniciais de operação [config: proposta], a validar em canário, não conclusões estatísticas nem valores atuais. O cutoff de pesquisa deve ser derivado da maior necessidade dos consumidores ativos e dos manifests protegidos; nenhum preset de idade pode sobrepor essa proteção.

### Primeiro expurgo controlado

Começar pela telemetria antiga e pelas entradas comprovadamente finalizadas da fila. Processar um grupo por vez, fora da execução de treino. Lock exclusivo por política, transações pequenas, checkpoint durável, ordem determinística e interrupção se houver atraso do banco, timeout, disputa de locks ou aumento anormal de WAL. Nenhum `TRUNCATE` ou `CASCADE` genérico.

Registrar cada lote: política/hash, consulta/hash, cutoff congelado, IDs ou manifesto dos objetos, quantidade candidata/removida/preservada, motivo de proteção, bytes físicos antes/depois, duração e status. Para arquivo: exportar, verificar contagem/checksum, testar leitura/restauração e só então excluir a origem. O log da retenção não entra na própria política de expurgo inicial.

### Automação permanente

Integrar ao Celery Beat existente, em fila de manutenção, com horários desencontrados das limpezas atuais e treinamento. A periodicidade inicial é horária [config: proposta `cadence_seconds=3600`], com limites ajustados à capacidade medida. Verificação diária de crescimento, tarefas atrasadas e dívida de retenção; aviso apenas em bloqueio, falha ou crescimento que ultrapasse a capacidade planejada. Isso é desenho da automação do produto; não foi criada uma automação de chat.

Reutilizar as tarefas atuais em vez de concorrer com elas. Particionar primeiro as tabelas novas de telemetria por tempo; expurgar partições vencidas depois de verificar suas dependências. Converter tabelas grandes existentes exige migração separada, capacidade temporária e ensaio de restauração.

### Aceitação e retomada do Pump

Comparar antes/depois: total e identidades de Shadow, resultados/P&L, gráficos de trades antigos, snapshots e versões, linha a linha dos datasets elegíveis L3/Pump, hashes de artefatos e pendências de labels. Validar cobertura de velas e fluxo. Nenhum treinamento, label ou parâmetro de estratégia deve ser alterado para fazer a verificação passar.

Depois de capacidade efetivamente comprovada, retomar captura e rotulagem, confirmar novos timestamps persistidos e executar o treino autorizado. Conferir resultado no ledger, modelo produzido e modelo efetivamente consumido. A lacuna histórica da captura não deve ser preenchida como se fossem observações point-in-time reais.

## Espaço físico e orçamento do Pump

O bloqueio soma `pg_total_relation_size` das tabelas do Pump. Excluir linhas pode liberar espaço interno para reutilização sem reduzir essa soma: não prometer que DELETE desbloqueará treinamento. O expurgo de uma tabela fora desse conjunto também não muda o limite interno do Pump.

Usar autovacuum/VACUUM normal para reutilização, acompanhar tamanho físico e ocupação separadamente. Não rodar `VACUUM FULL` na rotina: ele reescreve a tabela, exige espaço temporário e lock exclusivo. Ver [documentação PostgreSQL](https://www.postgresql.org/docs/current/routine-vacuuming.html). Recompactação e particionamento são projetos controlados, não parte implícita do primeiro expurgo.

A proposta anterior de ampliar capacidade continua pendente. O levantamento não demonstra que exista espaço expurgável suficiente para dispensar essa ampliação. Tamanho removível e economia financeira: **NÃO DISPONÍVEL** até simulação indexada e medição física.

## Referências do código

- `backend/app/services/mtf_calibration_service.py`: consulta temporal de indicators para L1/L2 dos Shadow concluídos.
- `ml_trainer/job.py` e `backend/app/ml/dataset_policy.py`: população de treino em shadow_trades.
- `backend/app/ml/l3_managed_exit.py`: decisões de saída e velas de fronteira.
- `backend/app/api/shadow_trades.py`: gráfico histórico via ohlcv.
- `backend/app/services/pump_ml_selection.py`: observações/labels/velas.
- `backend/app/services/pump_ml_candles.py`: ohlcv, universo de observações e pump_flow_asof.
- `backend/app/services/pump_opportunity_service.py`: fila pendente, label, diagnóstico de bloqueios e orçamento físico.
- `backend/app/services/pump_radar_indicator_summary.py`: dependência dos relatórios Radar e identidade de Shadow.
- `backend/app/tasks/prune_indicators.py`, `prune_indicator_snapshots.py`, `shadow_l3_continuation.py`, `collect_research_ohlcv.py`, `pump_monitor.py`: rotinas existentes.

## Ledger de evidências

- Tamanhos: valores literais na tabela acima e em `database-inventory.json`; GB = bytes / 1000000000 [calc]. Não somar tamanho de tabela novamente ao seu total, que já inclui índices.
- Data da captura e total do banco: `cutoff.rows` no mesmo arquivo [query].
- Configurações: JSON literal em `eligibility.json`, chave `config_extracts` [config: DB].
- Fila: `rows=2459184; completed=2367833; pending=91351; blocked=0`; conferência independente da decomposição: `2367833 + 91351 = 2459184` [query/calc].
- Observações: `rows=410225; older_than_configured_30d=0`; primeira `2026-10-01 16:38:00+00:00`, última `2026-10-08 20:58:00+00:00` [query].
- Expurgo vigente de indicators: `deleted=3042 batches=1 retention_days=90 hit_cap=False` [log]; fonte `prune-indicators.log`.
- Janelas/limites sugeridos: JSON da configuração inicial [config: proposta], sem alegação de existência em produção.
- Volume expurgável, bytes devolvidos ao disco, economia e tempo de recuperação: **NÃO DISPONÍVEL**.
