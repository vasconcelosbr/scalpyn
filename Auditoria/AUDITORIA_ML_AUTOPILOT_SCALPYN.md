# AUDITORIA TÉCNICA — Sistema de ML, Auto-Pilot e Market Data Hub (Scalpyn)

> Documento de auditoria para execução pelo Claude Code.
> Fase atual: **AUDITAR e PLANEJAR**. Nenhuma alteração de código nesta fase.

---

## 0. CONTEXTO E REGRAS ABSOLUTAS

Você é um auditor técnico sênior de sistemas quantitativos. Sua tarefa nesta fase é
**AUDITAR e PLANEJAR**, não modificar. NENHUMA alteração de código, schema, rota,
tabela ou config nesta fase.

**Entregável:** relatório de auditoria + plano de execução faseado + especificação dos
guardrails de autonomia do Auto-Pilot.

**Ambiente:** conta de teste com capital isolado e limitado. Não há capital em produção
em risco além do alocado para testes. A autonomia de escrita do Auto-Pilot no profile
L3 está **AUTORIZADA** pelo dono do sistema — o objetivo do plano é torná-la **segura e
observável**, não bloqueá-la.

### Regras de trabalho (inquebráveis)
- **VALIDE o código existente antes de qualquer afirmação.** Leia o arquivo real; não
  presuma comportamento por nome de função.
- Use **caminhos de arquivo EXPLÍCITOS** em todas as referências e no plano.
- Toda recomendação deve ser **ADITIVA** (não remover/alterar código, rotas, tabelas,
  componentes ou dados existentes — apenas estender).
- Diferencie no relatório: **FATO** (verificado no código) / **HIPÓTESE** (não
  confirmada) / **RISCO**. Marque **nível de confiança** e **severidade** em cada achado.
- Onde não conseguir verificar, declare **"não verificável sem X"** — não invente.
- Toda config persistida como **JSONB em config_profiles** — princípio Zero Hardcode.

### Caminhos de referência conhecidos
- `celery_app.py` está em `backend/app/tasks/celery_app.py` (NÃO em `backend/app/core/`).
- Documentação ML: `docs/ML_PIPELINE.md`, `docs/ml-deploy/`, `docs/13-scoring-ml`.
- Runbooks relevantes: `backend/docs/runbooks/` (pipeline-recovery, scheduler-locking,
  symbol-ingestion-audit, pool-execution-gate).
- Definição de label: `WIN_FAST = outcome='tp' AND holding_seconds <= ml_win_fast_threshold_seconds`
  (threshold em `config_profiles` como `ml_win_fast_threshold_seconds`, nunca hardcoded).

---

## PRIORIDADE 0 — INVESTIGAR 4 SUSPEITAS CRÍTICAS (FAÇA PRIMEIRO)

> Estas suspeitas vêm de evidência visual do sistema em produção de teste. São o ponto
> de partida da auditoria — não varredura genérica.

### 0.1 — Validade estatística do XGBoost (modelo win_fast v15)
Evidência: modelo v15 reporta RECALL=100% + CAPTURE=100% + FPR=100% simultâneos;
Train=146 / Val=31 / Test=32; n_pos=55 / n_neg=91; winrate_base=37.67%; threshold=0.482.

Localize: pipeline de treino, split train/val/test, cálculo de métricas, definição de
WIN_FAST. Investigue **com evidência de código**:
- **Causa exata** de RECALL=100% + CAPTURE=100% + FPR=100% ao mesmo tempo. FPR=100%
  indica classificação trivial (modelo prevê "positivo" para tudo) ou erro de cálculo
  de métrica. Identifique a fonte no código.
- **Data leakage:** (a) o split é temporal ou aleatório? (b) features usam apenas dados
  disponíveis no momento da decisão, ou incluem dados do desfecho (janela que contém o
  outcome)? (c) o threshold 0.482 foi escolhido em validação separada ou no test set?
- **Volume:** com ~12 positivos no test set, calcule o intervalo de confiança aproximado
  do win rate e declare se as métricas são confiáveis.
- **Scaling/normalização** vaza estatística do conjunto inteiro (fit no dataset completo
  antes do split)?
- Marque como achado de **VALIDADE** (não de capital): mesmo em teste, métricas sobre
  dados insuficientes/contaminados não validam a lógica.

### 0.2 — Gate de EV do Auto-Pilot (CRÍTICO)
Evidência: log do Auto-Pilot (L3) mostra `approved_ev = -0.1125` (NEGATIVO) classificado
como `performance_acceptable`; `approved_win_rate = 0.4495`; `fpr = 0.5505`; "última
mutação" vazia e regressões 0/3 em 30 dias.

Localize a lógica de decisão (cálculo de approved_ev, rejected_ev, classificação
`performance_acceptable`, estados SIDEWAYS/mutação/regressão). Investigue:
- **EV negativo aprovado é BUG?** Trace a fórmula de EV e o critério de aceitação no
  código. EV negativo nunca deveria ser `performance_acceptable`. Verifique inversão de
  sinal nos operadores de comparação (>, <, >=) do gate.
- **Por que nenhuma mutação em 30 dias?** Os thresholds de mutação são inalcançáveis
  (dead path de fato), ou o sistema realmente nunca encontrou gatilho?
- **Reconcilie** approved_win_rate (0.4495) vs winrate_base (0.3767) vs EV negativo: o
  win rate sobe mas o EV é negativo → sugere TP/SL (risk/reward) mal modelado no cálculo
  de EV. Confirme onde TP/SL entram na fórmula.

### 0.3 — Consistência label ↔ EV ↔ score
Verifique se a definição de WIN_FAST, o cálculo de EV do Auto-Pilot e o objetivo que o
Score Engine otimiza estão alinhados, ou se otimizam metas conflitantes.

### 0.4 — Look-ahead bias na integração Market Data Hub (liga ao 0.1)
Ver seção H. Esta é a causa-raiz mais provável do FPR=100% se houver join temporal
incorreto entre indicadores externos e trades históricos. Trate como CRÍTICO.

---

## ESCOPO COMPLETO DA AUDITORIA (após P0)

### A. Funil multi-stage de análise
Stage 0 (Pool / universe filter) → L1 (filtro primário) → L2 (Score Engine) → L3
(acquisition queue). Para cada estágio: estado real vs documentado, race conditions,
e se os filtros são aplicados na ordem correta. Pools: CINDACTA (spot), RADAR (futuros),
TradFi (macro, sem execução).

### B. Indicadores
Valide o cálculo de cada indicador usado no scoring (ema_trend, bb_width, adx,
vwap_distance_pct, taker_ratio, RSI, ATR, e demais):
- Correção matemática de cada fórmula.
- Tratamento de NaN / janela insuficiente.
- RSI_MAX gate (recomendação histórica: RSI_MAX = 70) está ativo e gating entradas?
- Indicadores zerados silenciosamente (problema histórico conhecido em EMA/ATR — ver
  análises de CAKEUSDT/SUIUSDT).

### C. Score Engine
Audite as scoring rules (ref. tela Score Engine Configuration):
- **Ranges sobrepostos ou com gaps.** Atenção especial: `bb_width between 0,0–0,0`
  (range degenerado / possível erro de digitação) e `bb_width between 0,0–0,1`.
- Regras que nunca disparam (dead rules).
- Soma de pesos por categoria (market structure, momentum, liquidity).
- Comparação long_score vs short_score (profiles de futuros).

### D. Decision Log
- Completude: todo trade/decisão é logado?
- Atomicidade da escrita.
- Decisões silenciosamente descartadas sem registro.

### E. Shadow Trades
- Paridade EXATA entre a lógica do shadow e a do path real (feature engineering +
  timing). Divergência aqui contamina a base estatística do ML.
- Como shadow trades alimentam o dataset de treino.

### F. XGBoost (além de 0.1)
- Versionamento de modelo e reprodutibilidade (MLflow run_id, artefato em GCS).
- Fallback se a predição falhar (o pipeline degrada graciosamente?).
- Threshold por-modelo em config_profiles (nunca hardcoded).

### G. Auto-Pilot (além de 0.2)
- Lógica de mutação de range/pontuação.
- Mecanismo de rollback / regressão (contador 0/3).
- Locking do scheduler nos horários 00:30, 07:45, 13:00, 21:15 UTC (sem execução dupla).
- Idempotência dos ciclos.

### H. Integração Market Data Hub (mdatahub.scalpyn.com)
Fonte externa: **Yahoo Finance + CoinMarketCap**, refresh a cada 5min, consumida via API
para enriquecer features de treino do ML.

**CAPTAÇÃO (a integração funciona?)**
- Localize o cliente/serviço que consome `mdatahub.scalpyn.com` e mapeie quais
  indicadores são captados (mkt cap, 24h volume, fear & greed, altcoin season, etc.).
- Os dados estão chegando e sendo persistidos? Verifique tabela/coleção de destino,
  frequência de gravação e últimos timestamps. Há registros nulos/ausentes?
- Tratamento de falha de API: o que acontece em downtime, erro ou timeout do mdatahub?
  Há retry, fallback, ou o pipeline grava NaN/zero silenciosamente?
- Rate limiting e caching: o refresh de 5min é respeitado ou há chamadas redundantes?

**INTEGRIDADE TEMPORAL (os dados são válidos para treino? — CRÍTICO, liga ao 0.1/0.4)**
- **LOOK-AHEAD BIAS:** cada trade histórico é associado ao valor do indicador disponível
  NAQUELE instante, ou a um valor mais recente/atual? Trace o join temporal no código de
  montagem do dataset. Causa-raiz suspeita do FPR=100%.
- **DISPONIBILIDADE NA INFERÊNCIA:** os indicadores estão garantidamente disponíveis no
  momento da decisão em produção, com a mesma latência do treino? Ou o modelo treina com
  dados que não terá ao decidir?
- **BACKFILL/REVISÃO:** Yahoo/CMC podem revisar dados históricos. O sistema armazena o
  valor *as-of* (no momento da captação, snapshot imutável por timestamp) ou re-lê
  valores que podem ter sido revisados?
- **ALINHAMENTO de granularidade:** indicadores de 5min associados a trades de timeframe
  diferente — há interpolação/forward-fill que introduz suavização irreal?

Marque achados de leakage temporal como severidade **CRÍTICA** — contaminam todo o
treino e podem explicar as métricas degeneradas do v15.

---

## ENTREGÁVEL

### 1. RELATÓRIO DE AUDITORIA
Achados organizados por seção. Cada achado com:
- Classificação **[FATO / HIPÓTESE / RISCO]**
- **Nível de confiança**
- **Caminho:linha** do arquivo
- **Severidade** (crítico / alto / médio / baixo)

### 2. PLANO DE EXECUÇÃO FASEADO (para o Claude Code)
- Additive-only, caminhos explícitos, ordenado por severidade.
- **Pré-requisitos obrigatórios antes de ativar autonomia de escrita:**
  - Correção do bug de **gate de EV negativo (0.2)** — caso contrário o Auto-Pilot
    propaga o bug ao escrever no L3.
  - Se a auditoria confirmar **leakage temporal no Market Data Hub (H/0.4)**, a correção
    do join as-of/temporal é pré-requisito para confiar em qualquer métrica de ML —
    listar **antes** do retreino do modelo.
- **NÃO executar** — apenas especificar.

### 3. ESPECIFICAÇÃO DOS GUARDRAILS DE AUTONOMIA
Auto-Pilot com escrita no profile **L3 ID `29155eda-6d8f-4abf-9f58-b3999ba9c878`**.
Projetar (additive, config em JSONB, nunca hardcoded):

- **Escopo de escrita permitido:** alterar range de score, alterar pontuação de regras,
  inserir filtros de bloqueio de trades no profile L3 alvo. Definir explicitamente o que
  está **FORA** do escopo (não tocar em outros profiles; não remover regras existentes —
  apenas adicionar/ajustar).
- **Limites por mutação:** delta máximo por ciclo (variação máx. de pontos/ranges por
  execução) para evitar swings bruscos.
- **Gate de EV mínimo:** nenhuma mutação que aprove conjunto com EV esperado negativo
  (depende de 0.2 corrigido).
- **Rollback automático:** critério de regressão que reverte a última mutação (relacionar
  ao contador 0/3 existente).
- **Kill-switch:** flag em config que desativa a autonomia de escrita instantaneamente.
- **Modo dry-run/shadow:** simular a mutação e logar o que SERIA feito antes de escrever
  de fato.
- **Auditabilidade:** toda mutação registrada (antes/depois, EV antes/depois, timestamp,
  ciclo) no decision log do Auto-Pilot.
- **Persistência:** como tudo acima é armazenado em config_profiles / JSONB sem hardcode.

---

## ORDEM DE EXECUÇÃO RECOMENDADA
1. P0 (0.1 → 0.2 → 0.3 → 0.4) com evidência de código.
2. Seções A–H.
3. Consolidar relatório (entregável 1).
4. Montar plano faseado (entregável 2).
5. Especificar guardrails (entregável 3).

**Não escreva nenhuma mudança de código nesta fase. Apenas audite, documente e planeje.**
