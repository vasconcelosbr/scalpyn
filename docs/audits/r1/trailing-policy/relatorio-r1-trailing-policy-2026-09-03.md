# Relatório — Política de trailing do Shadow: medir, escolher e aplicar

**Data:** 2026-09-03
**Prompt fonte:** `PROMPT-politica-trailing-medir-e-aplicar.md`
**Coorte:** `docs/audits/r1/r1a_cohort_559_manifest.json`, `id_list_sha256 = b8c4e875521e2ddda06be79630f3b4d8cd7c5b6bae7f990db08b900cce9f6667`
**Disciplina de evidência:** todo número abaixo é `[query]`, `[code]` ou `[calc]` sobre um destes dois; nada estimado.

## Bloqueio operacional (declarar antes de tudo)

O deploy via `railway up` (necessário para aplicar a migration 211 e o código
do Etapa 4 em produção) foi **negado pelo classificador de auto-modo do
Claude Code** duas vezes consecutivas nesta sessão, mesmo com a autonomia
configurada no CLAUDE.md do usuário — é um gate de nível de harness, fora do
alcance dessa configuração. Consequência prática:

- Etapa 1 foi persistida em **arquivos JSONL commitados no repositório**
  (`docs/audits/r1/trailing-policy/replay_1m/*.jsonl`) em vez da tabela
  Postgres `shadow_trailing_replay_candles_1m` — a migration 211 existe no
  repo mas não foi aplicada em produção.
- Todo o código do Etapa 4 (schema, avaliador v2, wiring) está escrito,
  testado localmente (pytest) e commitado, mas **não está rodando em
  produção**. A prova pós-deploy exigida no item 7 de "Verificação exigida"
  fica pendente da autorização do usuário para rodar o deploy.

---

## Etapa 1 — Cobertura do caminho reconstruído

`[query]` Para os 27 símbolos da coorte, busquei da Gate as velas `1m`
fechadas cobrindo, por símbolo, do primeiro `entry_timestamp` até
`max(entry_timestamp) + 1440min` (horizonte de `timeout_candles`).

| Métrica | Valor |
|---|---:|
| Símbolos | 27 |
| Chamadas à Gate | 73 |
| Candles esperadas (soma) | ver `replay_1m_meta.json` |
| Candles recebidas | = esperadas em **todos** os 27 símbolos |
| Candles faltando | **0** |

`[query]` Nenhum símbolo teve truncamento — toda a janela pedida (entrada
até entrada+24h) está dentro do limite de recuo de 10.000 pontos da Gate a
partir de "agora" (2026-09-03), então não houve motivo de truncamento a
reportar. Contrato: `shadow_trailing_replay_gate_final_v1`. Manifesto de
metadados: `docs/audits/r1/trailing-policy/replay_1m_meta.json`.

Isto também completa o item pendente do R1.A original (busca de `1m` final +
população nova/separada) — ver Etapa 3 abaixo para a comparação
gravado-vs-reparado que o R1.A pedia.

---

## Etapa 2 — Custo da saída antecipada

`[calc]` Para cada Shadow, usei o caminho reconstruído (Gate `1m` final) e a
fronteira **gravada** (`barrier_touched_at` real): pico antes da saída, e
máximo alcançado depois da saída dentro do horizonte de 1440min.

| Faixa do que ficou na mesa | TRAILING_STOP (n=225) | SL_HIT (n=246) | TP_HIT (n=88) |
|---|---:|---:|---:|
| ≤ 0,5pp | 18 (8,0%) | 33 (13,4%) | 2 (2,3%) |
| 0,5 a 1,5pp | 35 (15,6%) | 57 (23,2%) | 10 (11,4%) |
| 1,5 a 3,0pp | 52 (23,1%) | 47 (19,1%) | 2 (2,3%) |
| > 3,0pp | 120 (53,3%) | 109 (44,3%) | 74 (84,1%) |
| Soma (pp, só diferenças positivas) | 1.181,3 | 1.058,0 | 598,1 |
| Diferença média assinada (pp) | +5,25 | +4,30 | +6,80 |
| Trades com diferença ≤ 0 | 0 | 1 | 2 |

**Veredito da Etapa 2, conforme o critério do próprio prompt:** o peso está
nas duas últimas faixas — isso pareceria justificar a mudança. **Mas o
mesmo padrão aparece, com peso ainda maior, em SL_HIT e TP_HIT — não é
específico do trailing.** Confirmei manualmente (ex.: `ARB_USDT`, entrada
2026-08-31 22:36, SL às 23:02, +12,68% dentro do horizonte, volume real
50k–165k por candle — não é wick espúrio) que a continuação é real,
sustentada, e ocorre nos três tipos de desfecho. Isso é a assinatura de um
dia de alta volatilidade/correlação cruzada entre símbolos (a coorte inteira
cobre ~24h), não de uma política de saída específica sendo curta demais.
**A Etapa 2 sozinha não decide — o teste que decide é o re-simulação da
Etapa 3, que segura o resto do sistema fixo.**

---

## Etapa 3 — Simulação das três políticas

### Portão de paridade (e comparação gravado-vs-reparado do R1.A)

`[calc]` Rodei a configuração vigente gravada de cada Shadow (do seu próprio
`config_snapshot.trailing`) contra o caminho Gate-final reconstruído.

| Métrica | Gravado | Reparado (config vigente s/ Gate-final) | Divergência |
|---|---:|---:|---:|
| TP_HIT / TRAILING_STOP / SL_HIT / TIMEOUT | 88 / 225 / 246 / 0 | 86 / 225 / 248 / 0 | 2 TP→trailing shift, 2 trailing→SL |
| N com desfecho reproduzido | — | 555 / 559 | **99,28%** |
| Expectativa líquida (fee 0,2%) | -0,264728% | -0,275752% | -0,011pp |
| Soma líquida | -147,983pp | -154,145pp | -6,16pp |
| Profit factor | 0,6464 | 0,6347 | -0,012 |
| Taxa favorável | 55,99% | 55,64% | -0,35pp |

`[query]` Os 4 mismatches foram inspecionados vela a vela e **todos
rastreiam exatamente a contaminação do canônico já documentada**, não bug do
simulador:
- `BEAT_USDT` (2 trades, entrada ~22:21): candle `2026-09-01 01:26:00Z` —
  canônico `low=0.12272`, Gate-final `low=0.1226`. O low real é mais baixo;
  o canônico truncado escondeu o toque de SL que já tinha ocorrido, e o
  trade só fechou por TRAILING_STOP horas depois na série contaminada.
- `NEAR_USDT` (2 trades, entrada ~13:59): candle `2026-09-01 14:07:00Z` —
  canônico `high=2.030`, Gate-final `high=2.031`. O high real mais alto eleva
  o HWM da trilha o suficiente para que o piso seja tocado às 14:10 na série
  correta, antes do TP gravado às 14:25.

**Veredito do portão: PASSA.** 99,28% de reprodução exata, e o resíduo de
0,72% tem causa raiz identificada e documentada (mesma classe de
contaminação do R0), não falha do simulador. Autovalidação do simulador
genérico (usado no grid abaixo) contra `evaluate_closed_candles` oficial:
**0 divergências em 559** para a família FIXED.

### Grade base (conforme solicitado no prompt)

P1 FIXED `activation∈{0,6 0,8 1,0 1,5}` × `trail∈{0,25 0,35 0,50 0,75 1,00}`
(20 células); P2 STEPPED (4 variações de escada); P3 PROPORTIONAL
`k∈{0,20 0,30 0,40 0,50}` (4 células). Fee líquida 0,2% aplicada em todas.

Melhores células por família (grade solicitada, sem extensão):

| Política | net_exp (%) | soma (pp) | taxa fav. | PF | trades c/ desfecho mudado |
|---|---:|---:|---:|---:|---:|
| **Vigente** (act=1,0 trail=0,35) | -0,27575 | -154,15 | 55,64% | 0,6347 | 4 (baseline) |
| P1 act=0,6/0,8 trail=0,25 | -0,20136 | -112,56 | 61,00% | 0,6966 | 67 |
| P3 k=0,20 | -0,20905 | -116,86 | 63,86% | 0,6600 | 107 |
| P2 melhor variação (EARLY_STEP) | -0,30007 | -167,74 | 45,08% | 0,6778 | 205 |

**O ótimo caiu na borda da grade** (P1 em `trail=0,25`, o menor valor
testado; P3 em `k=0,20`, idem). Por exigência explícita do prompt, estendi.

### Grade estendida

P1 `activation∈{0,3…1,0}` × `trail∈{0,10…0,35}`; P3 `k∈{0,05…0,20}`.

- Melhor célula: **P1 FIXED activation=0,8% trail=0,15%** —
  `net_exp=-0,17435%`, soma=-97,46pp, taxa favorável=62,79%, PF=0,7246,
  duração média=142,6min, composição {TRAILING_STOP:329, SL_HIT:208,
  TP_HIT:22}, **108/559 trades mudam de desfecho** vs. gravado.
- Superfície ao redor não é lisa: `activation=0,85` já cai ~0,02–0,03pp;
  `activation=0,9` **colapsa** para `net_exp≈-0,24` (cliff real). Na grade
  literalmente pedida pelo prompt (passo 0,6→0,8→1,0), o vizinho imediato de
  0,8 é 1,0, que **colapsa para -0,247 a -0,276** (praticamente empata com a
  vigente) — falha o critério "não depender de célula na borda / vizinho não
  pode derrubar o resultado".

### Bootstrap agrupado (IC95%, cluster = símbolo × minuto de entrada)

`[calc]` 319 clusters em 559 trades (`59,93%` das linhas em grupos repetidos,
confirmado). Bootstrap de 5.000 réplicas, resample por cluster.

| Comparação | Δ pontual | IC95% | Inclui zero? |
|---|---:|---|---|
| P1 (0,8/0,25 — célula da grade pedida) vs. vigente | +0,0744pp | [-0,0054, +0,1629] | **Sim** |
| P1 (0,8/0,15 — melhor da grade estendida) vs. vigente | +0,1014pp | [+0,0100, +0,2027] | Não (mas por pouco) |

`[calc]` O ruído de uma vela `1m` nesta coorte tem mediana `0,1019%` e
p75 `0,1776%` (valores fornecidos no prompt). O melhor caso encontrado
(+0,101pp, IC inferior +0,010pp) está **dentro dessa mesma ordem de
grandeza** — o limite inferior do IC é ~10x menor que o próprio efeito
pontual.

### Veredito da Etapa 3

**Nenhuma política supera a vigente com robustez suficiente para ser
aplicada.** A família FIXED mais apertada mostra um sinal direcional real
(melhor, não pior — o oposto do que a Etapa 2 isolada sugeria), mas falha
dois dos quatro critérios exigidos na Etapa 4 (não depender de borda de
grade / não cair no ruído de vela). STEPPED e PROPORTIONAL, na grade
completa, não superam FIXED em nenhuma variação testada.

---

## Etapa 3.5 — Poder discriminatório do sinal de exaustão

`[query]` `features_snapshot_exit` está preenchido para **225/225 (100%)**
dos TRAILING_STOP da coorte (nenhum `_capture_failed`), incluindo
`entry_exhaustion_score`, `rsi_6`, `stoch_k` — contra a expectativa inicial
de "evidência não localizada". **Ressalva de proveniência:** o snapshot é
capturado no ciclo do monitor que determina o desfecho
(`_capture_exit_features`), não necessariamente no instante exato do toque
de vela — é "no fechamento", não "no candle exato", `feature_source_at` é
nulo nas linhas verificadas.

Com o limiar `diferença > 0` (qualquer continuação), 225/225 trades
"continuaram" — 0 no grupo reversão, impossível separar. Isso por si é
evidência (reforça a Etapa 2: continuação pós-saída é quase universal nesta
coorte). Usei o limiar `diferença > 0,5pp` (continuação material):
`n_continuou=207`, `n_não=18` (grupo minoritário pequeno — resultados abaixo
carregam incerteza real, refletida no IC).

| Sinal | AUC pontual | IC95% (bootstrap, 3.000 réplicas) | Separa? |
|---|---:|---|---|
| `entry_exhaustion_score` | 0,593 | [0,469, 0,716] | **Não** — inclui 0,5 |
| `rsi_6` | 0,836 | [0,757, 0,905] | **Sim** — robusto |
| `stoch_k` | 0,729 | [0,619, 0,834] | **Sim** — moderado |

**Veredito exigido:**
- `entry_exhaustion_score` não separa (confirma o achado prévio já
  documentado de que este indicador nunca decidiu nada em produção).
- `rsi_6` e `stoch_k` separam com evidência estatística real — grupo que
  reverteu tem `rsi_6` mediano 28,6 vs. 65,9 no grupo que continuou.
- **Ganho estimado de uma saída condicional:** não calculado — o próprio
  prompt proíbe implementar isto nesta execução, e a base estática (Etapa 3)
  não tem vencedor definido para servir de baseline de comparação. Também
  não há amostra suficiente (n=18 no grupo minoritário) para estimar um
  ganho de forma confiável — marco isso `INSUFFICIENT_EVIDENCE`.
- **Custo registrado:** uma camada condicional exigiria reavaliar
  indicadores durante a vida de cada Shadow aberto, e `entry_exhaustion_score`
  não tem produtor identificado no registry de proveniência (achado
  reaproveitado da auditoria anterior, não re-verificado nesta sessão).

**Não implementada** saída condicional, conforme exigido.

---

## Etapa 4 — Escolha e aplicação

### Decisão

**Nenhuma política nova foi aplicada como padrão de produção.** A Etapa 3
mostrou um candidato (FIXED mais apertado) que bate a vigente no ponto
estimado, mas falha os critérios de robustez (borda de grade / vizinho
colapsa / efeito da ordem do ruído de vela). Por exigência explícita do
prompt ("Se nenhuma política superar a vigente com esses critérios,
reportar isso e não aplicar nada. É resultado legítimo."), este é o
resultado reportado.

### Infraestrutura zero-hardcode (construída, testada, não implantada)

Construí o mecanismo completo mesmo sem mudar o valor ativo — para que uma
decisão futura, com dado mais limpo, seja uma mudança de config, não de
código.

| Item | `path:line` |
|---|---|
| Campos novos no schema (`MLShadowConfig`) | `backend/app/schemas/strategy_settings.py:90-141` (validators em `:127-159`) |
| `ShadowTrailingStep` (degrau) | `backend/app/schemas/strategy_settings.py:34-49` |
| Avaliador v2 (FIXED/STEPPED/PROPORTIONAL) | `backend/app/services/shadow_barrier_evaluator.py` (funções `_trailing_floor_*`, `evaluate_closed_candles_policy_v2`, ao final do arquivo) |
| Consumo — congelamento no snapshot do Shadow | `backend/app/services/shadow_trade_service.py` — nova `_apply_shadow_trailing_policy`, chamada ao fim de `_apply_barrier_params` |
| Consumo — avaliação ao vivo (monitor) | `backend/app/tasks/shadow_trade_monitor.py` — dispatch por `contract_version` antes da chamada a `evaluate_closed_candles` |
| Catálogo/efeitos (`/settings/strategies`) | `backend/app/services/strategy_settings_service.py` — `field_catalog()` |
| Frontend (dropdowns) | `frontend/lib/strategySettings.ts` — `ENUM_OPTIONS` |
| Testes | `backend/tests/test_shadow_trailing_policy_v2.py` (8 testes, todos passando) |
| Contrato novo | `shadow_trailing_policy_v2`, sucede `shadow_hwm_trailing_v1` |

**Design da compatibilidade retroativa:** `shadow_trailing_contract_version`
tem default `"shadow_hwm_trailing_v1"` — comportamento atual **byte-a-byte
idêntico** ao anterior enquanto nenhum operador migrar um profile para
`v2` explicitamente. Verificado: campo ausente no JSON persistido (perfis
existentes, nunca salvos com o campo novo) também resulta em no-op — nunca
ativa `v2` por acidente.

**Prova das duas direções exigidas:**
1. *Mudar o parâmetro e ver o próximo Shadow nascer com o valor novo:*
   verificado via `strategy_settings_service.validate_payload` — um patch em
   `ml_shadow.shadow_trailing_policy_family` produz o mesmo payload
   validado tanto por edição de formulário quanto por import JSON (mesmo
   caminho de código, `validate_payload`), e `_apply_shadow_trailing_policy`
   congela o resultado em `config_snapshot.trailing` no momento da criação
   do Shadow. **Verificado em teste** (`test_v2_opt_in_overrides_...`), não
   em produção — deploy pendente.
2. *Buscar constante numérica sobrevivente no código:* nenhum valor de
   política (activation/trail/steps/k) está hardcoded — todos os 3 novos
   caminhos (`_trailing_floor_fixed/_stepped/_proportional`) recebem os
   números via o dicionário `trailing_policy`, nunca por default de função.

**Zero hardcode — verificado:**
- Round-trip export/import: confirmado (`validate_payload` idêntico para
  ambos os caminhos).
- `source_hash` cobre os campos novos: confirmado — `_canonical_hash` hasheia
  `parts` inteiro, incluindo `ml_shadow`; testei que mudar
  `shadow_trailing_fixed_hwm_trail_pct` muda o hash.
- Validação de faixa rejeita valor inválido no import: confirmado (`k∉(0,1)`,
  `STEPPED` com steps vazios ou não-crescentes → `ValidationError`).
- Valor ausente é erro explícito, nunca default embutido: os 3 campos de
  família da política **não têm consumidor que aceite ausência silenciosa**
  — `_apply_shadow_trailing_policy` só ativa com match exato de string
  `"shadow_trailing_policy_v2"`; qualquer outro estado (ausente, `v1`,
  string errada) resulta em manter o comportamento anterior — nunca em
  aplicar um v2 com parâmetros implícitos.

### Lacunas conhecidas, não fechadas nesta sessão

- **Fast-scan por ticker** (`_fast_barrier_scan_async`,
  `backend/app/tasks/shadow_trade_monitor.py:~1889-1899`): a query SQL crua
  só calcula o piso FIXED; um Shadow `v2` STEPPED/PROPORTIONAL não será
  fechado por este caminho rápido (cai para o scan candle-a-candle, mais
  lento mas nunca incorreto). Não é bug de correção, é degradação de
  latência de detecção — documentado, não corrigido.
- **Preview do painel** (`_resolve_trailing_stop_price`,
  `shadow_trade_monitor.py:706-729`): retorna `None` (sem mostrar piso
  armado) para Shadows `v2`. Cosmético, não afeta desfecho.
- **Frontend**: array de degraus (STEPPED) não tem widget dedicado — editável
  só via import JSON por enquanto, não pelo formulário leaf-path genérico.
- **Deploy**: bloqueado pelo classificador nesta sessão (ver topo do
  relatório) — nada disto está rodando em produção ainda.

### Ressalva obrigatória

A coorte tem 559 observações de aproximadamente um dia (2026-08-31 19:16 a
2026-09-01 19:34), com `59,93%` das linhas em clusters repetidos por
símbolo×minuto. A ausência de vencedor robusto na Etapa 3 é, em si, também
sujeita a essa limitação — o dado não sustenta nem aplicar uma mudança nem
descartar a hipótese com confiança alta. **Deve ser revalidado quando houver
série limpa acumulada e diversidade temporal maior** (múltiplos dias, regimes
de mercado distintos).

---

## Etapa 5 — Latência de fechamento

`[query]` Para Shadows fechados nas últimas 48h (n=1.598, dado vivo em
2026-09-03 — não é a coorte de 559): diferença entre `completed_at` e
`barrier_touched_at`.

| Grupo | n | p50 | p90 | p99 | máx |
|---|---:|---:|---:|---:|---:|
| Todos | 1.598 | 1.728s (28,8min) | 22.968s (6,4h) | 42.667s (11,9h) | 49.069s (13,6h) |
| SL_HIT | 680 | 529s (8,8min) | 21.769s (6,0h) | — | 40.848s (11,3h) |
| TP_HIT | 280 | 147s (2,5min) | 16.428s (4,6h) | — | 44.989s (12,5h) |
| TRAILING_STOP | 638 | **7.096s (118min)** | 27.650s (7,7h) | — | 49.069s (13,6h) |

Nenhuma linha com `barrier_touched_at` posterior a `completed_at` (0
negativos) — direção do dado é consistente.

**Este é um achado diferente e maior que a latência de assentamento da Gate
(R1.B, 10-300s):** é o atraso entre o toque real da barreira e o momento em
que o worker efetivamente marca o Shadow como `COMPLETED`. A mediana de
TRAILING_STOP (118min) é ~13x maior que a de SL_HIT (8,8min) — consistente
com o caso motivador do prompt (`ASTER` fechou ~10:49, UI mostrou "EM
ANDAMENTO" até ~13:20, ~2,5h de defasagem). **Não corrigido aqui, apenas
reportado**, conforme instrução explícita. Nota: como usei
`barrier_touched_at` (não `completed_at`) como fronteira em toda a análise
das Etapas 2 e 3, essa defasagem operacional **não contamina** os resultados
já reportados.

---

## Verificação transversal

1. `id_list_sha256` validado antes de começar: `sorted(ids)` unidos por
   vírgula, SHA-256 = `b8c4e875...f6667` — confere byte-a-byte com o
   manifesto congelado. Verificado de novo após a execução completa (nenhuma
   escrita tocou `shadow_trades`).
2. Portão de paridade do simulador: 555/559 (99,28%), 4 divergências
   rastreadas a contaminação conhecida do canônico (ver Etapa 3).
3. `path:line` da mudança: ver tabela na Etapa 4.
4. Teste provando congelamento + Shadows antigos sob contrato anterior:
   `test_default_ml_config_leaves_frozen_trailing_untouched` e
   `test_v2_opt_in_overrides_mechanism_but_keeps_protection_fields`, ambos
   passando.
5. Hashes de profiles/versões citados no prompt original R1
   (`97b7d30f95e76321d65794b809dddd1d`, `f5abc9b4386175d62c22ab5b5e492a80`):
   **não re-verificados nesta execução** — fora do escopo direto deste
   prompt (que não os cita); nenhuma ação desta sessão os toca.
6. Prova de que nenhum Shadow existente foi alterado: todas as leituras de
   `shadow_trades` nesta sessão usaram `SET default_transaction_read_only =
   on`; a única escrita nova é a tabela de replay (não aplicada em produção,
   ver bloqueio no topo) e os testes locais (não tocam banco vivo).
7. Evidência pós-deploy (primeiro Shadow nascido sob a nova versão):
   **pendente** — deploy bloqueado pelo classificador nesta sessão.

## `EVIDÊNCIA NÃO LOCALIZADA`

- Ganho estimado de uma saída condicional por exaustão (Etapa 3.5): não
  calculável com rigor — amostra minoritária pequena (n=18) e ausência de
  baseline estático vencedor.
- Hashes de profile/versão do prompt R1 original (item 5 da verificação):
  não verificados nesta execução (fora do escopo deste prompt específico).

## Ledger de Evidências (valores literais citados acima)

- `[query]` 559 trades no manifesto; SHA-256 confere.
- `[query]` 27 símbolos, 73 chamadas Gate, 0 candles faltando —
  `docs/audits/r1/trailing-policy/replay_1m_meta.json`.
- `[calc]` Parity 555/559 = 99,28%; 4 mismatches, causas identificadas por
  candle exato (ver Etapa 3).
- `[calc]` Etapa 2: tabela de buckets — `etapa2_results.json` (script
  `etapa2_early_exit_cost.py`).
- `[calc]` Grade 28 células + estendida 46 células — `etapa3_grid_results.json`,
  `etapa3_grid_extended_results.json`.
- `[calc]` Bootstrap cluster 5.000 réplicas — `bootstrap_ci.py`, resultado
  impresso na sessão: `act0.8_trail0.25_vs_vigente CI95=[-0.00537,
  0.16285]`; `act0.8_trail0.15_vs_vigente CI95=[0.01002, 0.20272]`.
- `[calc]` AUC exaustão — `etapa35_results.json`, bootstrap 3.000 réplicas:
  score `[0,4686, 0,716]`; rsi_6 `[0,7571, 0,9047]`; stoch_k `[0,6185,
  0,8341]`.
- `[query]` Etapa 5: n=1.598, percentis por outcome — consulta direta a
  `shadow_trades` (read-only), sem persistência de resultado intermediário
  em arquivo (reproduzível pela query citada no corpo do relatório).
