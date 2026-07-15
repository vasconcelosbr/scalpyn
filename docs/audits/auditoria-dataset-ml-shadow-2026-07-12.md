# Auditoria Forense — Pipeline Shadow → DatasetBuilder → ML (v75/v77)

**Data:** 2026-07-12 | **Modo:** READ-ONLY (somente SELECT + inspeção de código) | **Banco:** produção Railway (proxy público)
**Escopo:** H1–H16 do prompt `PROMPT_AUDITORIA_DATASET_ML_SHADOW_COMPLETO.md` + Fases 7–18 (recuperação histórica).

Todos os números são valores literais de outputs de query colados ([query]), de código com path:linha ([código]) ou cálculos com insumos literais ([calc]). Nenhum número fabricado.

---

## 0. Correções de premissa do prompt (Fase 0 — schema discovery)

| Premissa do prompt | Realidade [query information_schema] |
|---|---|
| `closed_at` | coluna é `completed_at` |
| `status_l3` | coluna é `source` (L3, L3_REJECTED, L3_LAB, L1_SPECTRUM, L3_SIMULATED) |
| `net_pnl` / `result` | colunas são `net_return_pct`/`pnl_pct` e `outcome` ('TP_HIT'/'SL_HIT'/'TIMEOUT') |
| `decision_logs` (tabela) | **não existe** (0 colunas) |
| `entry_features_snapshot` | não existe; snapshot de entrada é `features_snapshot` |
| DatasetBuilder lê `decisions_log.metrics` | **FALSO** — lê `shadow_trades.features_snapshot` [código `ml_challenger_service.py:714-747`] |

**Anomalia central do prompt reavaliada:** o ratio "12,8 trades/snapshot" dividia `included_trade_count` (dataset inteiro, 3.972) pelo `effective_snapshots` de **um único split** (311 = validação do v77, não teste). Comparação inválida. Ratios reais por split [query ml_models]:

| Modelo | split | samples | effective_snapshots | trades/vetor [calc] |
|---|---|---:|---:|---:|
| v77 | val | 721 | 311,0 | 2,3 |
| v77 | test | 794 | 350,0 | 2,3 |
| v75 | val | 12.450 | 1.643,0 | 7,6 |
| v75 | test | 13.679 | 1.330,0 | 10,3 |

`effective_snapshots` = soma dos pesos `1/count(grupo)` onde grupo = sha256 do `features_snapshot` [código `ml_challenger_service.py:610,631`; `indicator_intelligence.py:10-13`; `_snapshot_group_key` em `ml_challenger_service.py:83-91`]. Ou seja: é a **contagem de vetores de features distintos do split** — métrica correta, já usada como peso amostral e no AUC ponderado.

---

## Vereditos H1–H7

### H1 — Fonte pobre de features
**Veredito: FALSA | Confiança: Alta**
- Builder lê `shadow_trades.features_snapshot` [código `_load_shadow_data`, `ml_challenger_service.py:723`].
- Riqueza (janela completed 01–07/jul) [query 2.1/2.1b]:
  - `decisions_log.metrics`: min 84 / mediana 92 / max 105 chaves
  - `shadow_trades.features_snapshot`: min 3 / mediana 89 / max 94
  - Por source: L3 mediana **82**, L3_REJECTED **89**, L1_SPECTRUM **92**, L3_LAB **14** (pobre — mas fora das lanes v75/v77)
- Critério de falsificação atendido (≥40 chaves nas lanes auditadas).
**Ressalva:** L3_LAB tem cache pobre (mediana 14) — se algum treino futuro usar L3_LAB, H1 vale lá.

### H2 — Join com perda silenciosa
**Veredito: FALSA | Confiança: Alta**
- O builder **não faz join** com `decisions_log` — não existe caminho de descarte por join.
- `decision_id` existe somente em L3 (3.221/3.221 = 100% de cobertura no join [query 1.2/1.4]; 1 trade por decision). L3_REJECTED (40.402), L1_SPECTRUM (1.216), L3_LAB (206) têm decision_id NULL **por design** [query 1.1].

### H3 — Granularidade errada / duplicação de features
**Veredito: PARCIALMENTE VERDADEIRA | Confiança: Alta**
- Duplicação real existe (janela completed 01–07/jul) [query 1.3c]:

| source | trades | vetores distintos | trades/vetor |
|---|---:|---:|---:|
| L3_REJECTED | 40.402 | 6.384 | 6,3 |
| L3 | 3.221 | 1.446 | 2,2 |
| L1_SPECTRUM | 1.216 | 1.204 | 1,0 |

- Origem: o mesmo snapshot de mercado (símbolo×ciclo de scan) é replicado entre profiles — 9.220 grupos multi-profile [query H15].
- **Mas o teto de AUC não colapsou**: vetores idênticos com labels conflitantes afetam 102/3.221 = 3,2% (L3) e 1.087/40.402 = 2,7% (L3_REJECTED) [query 1.5a/1.5b] — muito abaixo do critério de 30% do prompt. E dentro do mesmo profile o conflito é ~zero (1 grupo, 2 trades [query H15b]).
- O sistema **já mitiga** com `inverse_group_frequency_weights` e métricas ponderadas nas lanes INTELLIGENCE [código `ml_challenger_service.py:1999-2004`].

### H4 — Label errado
**Veredito: FALSA | Confiança: Alta**
- `net_return_pct = pnl_pct − fee` em **44.874/44.874** linhas com valores (100%); `fee_roundtrip_pct_applied = 0,2000` em todas as lanes com fee [query 3.1/3.1b].
- Label idêntico entre lanes: `label_version='positive_net_return_v1'` em v75 E v77 [query ml_models]; `ml_label_objective='positive_net_return'` [config: ml]; implementação `net_return > 0` [código `feature_extractor.py:425-426`].
- A diferença de positive_rate (0,4369 vs 0,2667) é explicada por contrato + regime + viés de maturidade (ver causa-raiz), não por definição de label.

### H5 — Tabela de decisão errada
**Veredito: FALSA | Confiança: Alta**
- `decision_logs` não existe [query 0.1]. `trade_decisions` quase morta (4–16 escritas/dia) vs `decisions_log` viva (centenas–milhares/dia) [query 4.1].
- Irrelevante para o dataset: o builder lê `shadow_trades` diretamente.

### H6 — Pipelines de build divergentes entre lanes
**Veredito: VERDADEIRA | Confiança: Alta** ← **um dos dois achados centrais**
- [código `ml_challenger_service.py:1907`]: `if cb_sources in (["L3"], ["L3_REJECTED"]) and cb_lane != "L3_APPROVED_INTELLIGENCE":` → o filtro `_filter_l3_barrier_contract` (modo ATR_DYNAMIC, tp 1,5 [config: shadow_barrier_mode/shadow_tp_pct]) é **pulado exatamente na lane do v77**.
- Consequência nos dados [query 5.2, C3]:
  - v75 (L3_REJECTED): **100% ATR_DYNAMIC tp=1,5** (41.032/41.032) — homogêneo.
  - v77 (L3): mistura **FIXED 1.0/1.0 (3.431) + FIXED 0.6/1.0 (380) + ATR_DYNAMIC 1.5/SL variável (~450)** — o mesmo label "net>0" significa alvos diferentes por contrato.
- Mitigação parcial existente: a lane APPROVED adiciona `tp_pct_applied, sl_pct_applied, reward_risk_ratio, break_even_probability, barrier_mode_encoded` como features [código 949-961] — mas features de contrato não removem a heterogeneidade do target.

### H7 — Filtro de janela/perfil descartando volume
**Veredito: FALSA | Confiança: Alta**
- Reconciliação do funil (janela created 01–11/jul) [query 5.1b vs report]:
  - L3_REJECTED: 68.374 elegíveis pela query do builder ≈ included 68.618 (bordas de janela; train+val+test = 66.442 → ~2,2 mil removidos por contract row-validation E7 + embargo, ambos logados).
  - L3: 3.750 ≈ included 3.972.
- Exclusões documentadas: `excluded_null_profile_id` = 9 (L3) / 30 (REJECTED) — consistente com profile_id NULL medido (9/30) [query 5.1b].

---

## Vereditos H8–H16 (extensões)

### H8 — Histórico descartado apenas por ausência de versionamento novo
**Veredito: VERDADEIRA | Confiança: Alta** ← **o segundo achado central**
- Desde 01/07: **83.067 finalizados; 2.393 elegíveis (2,9%); 80.674 inelegíveis** [query H8.1].
- Dos inelegíveis, a causa é lineage V2: **80.506 sem `profile_version_id`/`score_engine_version_id`** — mas **100% têm** `config_snapshot`, `features_snapshot` e `outcome` (83.067/83.067) [query H8.1].
- `eligible_for_training` é gravado **apenas no INSERT** (`lineage_complete and not feature_errors`) [código `shadow_trade_service.py:822-829, 929`]. O stamping V2 entrou em produção **2026-07-11 17:28 UTC** (primeiro trade elegível [query D1]) — todo o histórico anterior é inelegível por construção, não por mérito.
- Falhas materiais reais são raras: `INVALID_FEATURES` = 221, `VERSION_IDS_UNRESOLVED` = 52; o resto é `lineage_status = None` (nunca avaliado) [query H8.1c].
- **Potencialmente recuperáveis: 81.437** (L3_REJECTED 72.747 + L3 4.119 + L1 1.832 + L3_LAB 1.739) [query H8.2].
- Nota: hoje isso **não** limita os treinos advisory — `ml_predictive_gate_v2=false` [config: ml], então v75/v77 treinaram sem exigir lineage. O impacto de H8 é futuro: quando o gate v2 ligar, só 2,9% do histórico entra.

### H9 — Profile version reconstruível pelo config_snapshot
**Veredito: PARCIALMENTE VERDADEIRA | Confiança: Média**
- `config_snapshot` tem **apenas 8 chaves**: `amount_usdt, ml_fee_roundtrip_pct, sl_pct, timeout_candles, tp_pct, ttt_enabled, ttt_timeout_minutes, ttt_tp_pct` [query D4] — é o **contrato de execução do shadow**, não o config completo do profile (sem filters/signals/entry_triggers/scoring).
- `md5(config_snapshot::text)` **over-fragmenta**: `sl_pct` carrega o SL resolvido por ATR do trade (ex.: 2.4909, 0.6159, 0.50505) em parte das linhas → hashes singleton por trade [query V1]. Profile exemplo: 21 hashes, muitos com 1 trade; vários hashes "estáveis" coexistem sobrepostos no tempo [query V2].
- Conclusão: reconstrução determinística é viável **somente para o contrato de barreira normalizado** (tp base, timeout, ttt, fee — excluindo sl ATR-resolvido, que já existe por coluna dedicada `sl_pct_applied`). Para versão plena do profile, a fonte teria de ser `profile_versions` (que existe, com `config_hash`) — 31 de 54 `profile_config_hash` da app têm match em `profile_versions.config_hash` [query 8.4]; os hashes da app só existem em linhas novas (2.627 [query 7.2]).
- O hash SQL `md5(config_snapshot::text)` é **diagnóstico**, não idêntico ao hash canônico da aplicação.

### H10 — Score engine reconstruível pelas regras da decisão
**Veredito: PARCIALMENTE VERDADEIRA (tier B) | Confiança: Alta**
- L3: **4.284/4.284** trades com join em `decisions_log` têm `reasons` (objeto `{rule_<id>: 'OK'|'FAIL'}`), `score` e `metrics` [query H10a-retry/H10b].
- `reason_codes` em shadow_trades: 100% em L3, L3_REJECTED, L1_SPECTRUM, L3_SIMULATED; 0% em L3_LAB — que por sua vez tem `rules_snapshot` (1.739 = 100% dos L3_LAB finalizados) [query H10c-retry/9.1].
- O que NÃO se preserva no histórico: pontos por regra, score_max, thresholds versionados → reconstrução exata da Score Engine Version (tier A) impossível; **tier B (selected_rule_ids + estados OK/FAIL + score total) é viável para ~100% das linhas**. Score utilizável como feature (tier C) em todas.

### H11 — Snapshots de entrada e saída reais e distintos
**Veredito: VERDADEIRA (saudável) | Confiança: Alta**
- 83.067 fechados: 4.395 sem exit (5,3%), **78.577 distintos**, apenas **95 idênticos** (0,12%) [query 10.1].

### H12 — Timestamp da feature recuperável
**Veredito: PARCIALMENTE VERDADEIRA | Confiança: Média**
- `features_captured_at` só existe nas linhas novas (ex.: 2.898/75.636 em L3_REJECTED); onde existe, delta médio vs created_at = 0,1–49,2 s [query 11.1].
- Para o histórico: `features_snapshot` é imutável após INSERT (princípio 7 do CLAUDE.md) e `created_at` ≈ entrada → classificação **LIKELY_EX_ANTE por construção** [inferência: arquitetura de escrita, não medição direta]. Não há evidência de contaminação pós-outcome; `UNKNOWN_TEMPORALITY` estrito se exigir prova por coluna.

### H13 — TP/SL/timeout e custos recuperáveis por trade
**Veredito: VERDADEIRA | Confiança: Alta**
- Colunas dedicadas 100% populadas nos finalizados desde 01/07: `tp_pct_applied` 83.067, `sl_pct_applied` 83.067, `timeout_candles` 83.067, `ttt_timeout_minutes` 83.067, `barrier_mode` 83.067; `fee_roundtrip_pct_applied` 81.328 (97,9%) [query 12.1].
- Cross-check de label com custo já feito em H4 (100% de concordância net = pnl − fee).
- Contratos mistos identificados e separáveis por `(barrier_mode, tp_pct_applied)` [query 5.2].

### H14 — Features canonicalizáveis sem perda
**Veredito: INCONCLUSIVA (sem red flags nos ranges) | Confiança: Média**
- Ranges (L3+L3_REJECTED desde 01/07) [query H14]: `taker_ratio` 0,005–1,0 (fração ✓); `rsi` 4,89–93,42 (✓); `bb_width` 0,000228–2,031 (decimal, cauda alta a investigar); `atr_pct` 0–32,64 (% com outliers).
- Inventário completo de aliases (atr_pct vs atr_percent etc.) não foi executado — requer diff de conjuntos de chaves por período/source (listado em "não confirmado").

### H15 — Labels divergentes explicados pelo contexto do profile
**Veredito: VERDADEIRA | Confiança: Alta**
- 9.220 grupos de vetor compartilhados entre profiles; **162** com labels conflitantes (1.813 trades) [query H15].
- Conflito **intra-profile: 1 grupo / 2 trades** [query H15b] → o conflito é quase inteiramente **efeito legítimo do contexto** (SL ATR por perfil, contrato, timing), não ruído irredutível.
- Implicação de arquitetura: **modelo global contextual** (features de contrato + perfil) é a resposta certa — que é a direção já tomada pela lane APPROVED (contract features) e pelo `profile_id_encoded`.

### H16 — Builder V2 ignora histórico recuperável
**Veredito: VERDADEIRA | Confiança: Alta**
Respostas às 7 perguntas do prompt:
1. Elegibilidade de linha no builder: `outcome IN (TP_HIT,SL_HIT,TIMEOUT) AND pnl_pct IS NOT NULL AND features_snapshot não-vazio AND created_at >= valid_from [AND profile_id IS NOT NULL] [AND eligible_for_training=true se gate v2]` [código `_load_shadow_data:736-747`].
2. Histórico é descartado por: `eligible_for_training=true` (quando gate v2 ligar) — que só existe para INSERTs pós-11/jul.
3. Lane HISTORICAL_BACKFILLED_VERIFIED: **não existe**.
4. Builder aceita config hash reconstruído: **não**.
5. Builder aceita snapshot ID determinístico: parcialmente — `_snapshot_group_key` (sha256 do features_snapshot) é determinístico e usado para pesos/split, mas não para elegibilidade.
6. Diferencia canonical vs historical: **não** (binário eligible true/false).
7. Reason code de exclusão: **não** — `lineage_status` é gravado só no INSERT; o builder não emite ledger de descarte.

---

## Causa-raiz do v77 degenerado (AUC test 0,4797)

**Não é descarte nem degradação de dados na leitura do shadow.** Cadeia de evidências, por ordem de contribuição estimada:

1. **Drift de regime intra-janela + split cronológico** [query C2]: pos_rate diário L3: 0,556 → 0,605 → **0,723** (03/jul) → 0,387 → 0,528 → **0,372 → 0,365 → 0,366** (06–08/jul). O treino (created 01–05/jul, `train_to=2026-07-05 07:37` [query B]) aprendeu um regime que inverteu no teste. O v75 sofreu o mesmo drift mas com 17× mais dados e contrato homogêneo → segurou AUC 0,61.
2. **Mistura de contratos de saída (H6)**: o filtro de barrier contract é pulado na lane APPROVED [código 1907] → target heterogêneo (FIXED 1.0 / FIXED 0.6 / ATR 1.5).
3. **Amostra pequena e seletiva (Fase 18)**: 3.832 linhas, 25 profiles, e L3 é fatia selecionada do mercado — RSI mediano 61,1 vs 48,7 dos rejeitados [query F18]; range restrito degrada generalização. Volume L3 colapsou para 46/43 trades/dia em 09–10/jul [query 6.1].
4. **Viés de maturidade na cauda da janela** [query M1]: trades criados 09–11/jul e resolvidos antes do cutoff têm pos_rate 0,656 (L3) vs 0,339 dos resolvidos depois; em L3_REJECTED, 0,417 vs 0,578 (holding 154 vs 687 min). O fim do dataset (= test split) é enviesado pela velocidade de resolução. Nenhum embargo de maturidade existe no `_load_shadow_data`.

A duplicação de snapshots (H3) é real mas já é mitigada por pesos; com 2,3 trades/vetor e 3% de conflito, não explica AUC < 0,5 sozinha.

---

## Ledgers

### A. Historical Recovery Ledger (created_at >= 2026-07-01, snapshot 2026-07-12)

| Métrica | Valor | Origem |
|---|---:|---|
| Trades desde 01/07 | 83.788 | [query 7.1b: 75.636+4.282+1.884+1.783+203] |
| Finalizados | 83.067 | [query H8.1] |
| A_CANONICAL_V2 | 2.561 | [query 7.3: 2.248+150+121+42] |
| B_BACKFILL_CANDIDATE | 80.269 | [query 7.3: 72.730+4.119+1.831+1.589] |
| C_PARTIAL | 0 | [query 7.3] |
| D_INVALID | 958 | [query 7.3: 658+203+44+42+11] |
| Profiles: hashes múltiplos | regra, não exceção (11–21 hashes/profile no top-15) | [query 8.1] — inflado por sl_pct ATR no snapshot |
| profile_config_hash com match em profile_versions | 31 de 54 | [query 8.4] |
| Score engines reconstruíveis (tier B) | ~100% (reasons/reason_codes/rules_snapshot) | [query H10a/H10c/9.1] |
| Entry snapshots válidos | 83.067 (100%) | [query H8.1] |
| Exit snapshots válidos | 78.672 (94,7%) | [query 7.2] |
| Snapshots idênticos suspeitos | 95 (0,12%) | [query 10.1] |
| Labels recalculados concordantes | 44.874/44.874 (100%, janela 01–07) | [query 3.1] |
| Effective snapshots (completed 01–07) | L3: 1.446/3.221; L3_REJECTED: 6.384/40.402 | [query 1.3a/1.3b] |

### B. Exclusion Ledger (estado atual do sistema)

| reason_code | rows | % dos finalizados | justificativa |
|---|---:|---:|---|
| lineage_status=None (nunca avaliado; INSERT pré-11/jul) | 80.401 | 96,8% | [query H8.1c, calc] ausência de versionamento, não falha material |
| INVALID_FEATURES | 221 | 0,27% | falha material real |
| VERSION_IDS_UNRESOLVED | 52 | 0,06% | resolução de versão falhou no INSERT |
| ELEGÍVEIS (EXACT) | 2.393–3.017* | 2,9% | *2.393 em H8.1 (às 0x UTC); 3.017 em D1 (medição posterior — cresce ~7k/dia) |

### C. Versões históricas propostas (read-only)
Não emitir versões a partir do `md5(config_snapshot)` bruto — o hash embute `sl_pct` ATR por-trade [query V1]. Proposta: hash normalizado = `(tp_pct, timeout_candles, ttt_enabled, ttt_tp_pct, ttt_timeout_minutes, barrier_mode, fee)` com `sl` representado pelo multiplicador base (não pelo valor resolvido). Com isso, o exemplo de 21 hashes colapsa para ~1–3 versões reais por profile. Tabela final requer a normalização (não executada — seria escrita/ETL).

### D. Decisão por tier

- **A_CANONICAL_V2 (2.561, crescendo ~7 mil/dia desde 11/jul)** → full training eligibility (gate v2).
- **B (80.269)** → elegível após backfill determinístico: contrato por colunas dedicadas (H13 ✓) + hash normalizado (C) + `features_snapshot` imutável (H12 LIKELY_EX_ANTE). É o grosso do valor recuperável.
- **C (0)** → n/a.
- **D (958)** → excluir com reason code (658 L3_REJECTED sem completed, 203 L3_SIMULATED sem profile, etc.).

### E. Veredito final — 10 respostas

1. **Dos 4.282 L3 (UI mostra 4.280 — reconciliado, diferença = timing)**: recuperáveis 4.240 (121 A + 4.119 B) [query 7.1/7.3].
2. **Sem nenhum backfill**: 121 (A) — e todos os 4.240 já são usados hoje pelos treinos advisory (gate v2 off).
3. **Só versão/hash**: 4.119 (B).
4. **Sem temporalidade comprovável por coluna**: ~4.161 (sem `features_captured_at`), mas LIKELY_EX_ANTE por construção (snapshot imutável no INSERT).
5. **Realmente inválidos**: 42 (L3) / 958 (todas as sources).
6. **O 2.800 pode ser atingido com histórico?** O gate `ml_retrain_min_eligible_rows=2800` se aplica à lane **L1_SPECTRUM** contando **linhas brutas** pós-filtro [código 1730]. Com `ml_dataset_valid_from=2026-07-01` [config: ml], L1 tem 1.753 linhas (01–11/jul) [query 5.1b] → **não atingido e o histórico L3 não conta para esse gate**. Desde 01/06 a L1 teria 4.401 [query 5.1] — o reset do `valid_from` para 01/07 é o que zerou o relógio.
7. **Uso imediato**: 100% de A+B (84 mil linhas) já é utilizável pelos treinos advisory atuais; para gate v2 preditivo: só A (2,9%).
8. **Exige backfill**: 80.269 (B) — somente se/quando `ml_predictive_gate_v2=true`.
9. **Só descritiva**: 958 (D). C=0.
10. **Bloqueio real do retreino**: não é volume bruto. É: (a) drift de regime + ausência de embargo de maturidade na cauda da janela; (b) contrato heterogêneo na lane APPROVED; (c) para a lane L1: `valid_from` resetado → 1.753 < 2.800; (d) futuro gate v2 restringirá a 2,9% até haver backfill de lineage.

---

## Correção mínima proposta (ESTENDER código existente)

1. **H6 (1 condição):** aplicar `_filter_l3_barrier_contract` também quando `cb_lane == "L3_APPROVED_INTELLIGENCE"` (ou, alternativa preservando volume: particionar o dataset APPROVED por `(barrier_mode, tp_pct_applied)` e treinar/pesar por contrato). Local: `ml_challenger_service.py:1907`.
2. **Maturidade (1 cláusula):** em `_load_shadow_data`, adicionar embargo de maturidade — excluir trades com `created_at > cutoff − (ttt_timeout + margem)` OU exigir `label_resolved_at IS NOT NULL AND completed_at < cutoff − X`. Evita que a cauda do dataset selecione labels por velocidade de resolução [evidência query M1].
3. **H8/H16 (backfill de lineage):** estender o resolver de lineage para avaliar linhas históricas: marcar `lineage_status='HISTORICAL_BACKFILLED'` + `eligible_for_training=true` quando (profile_id ✓, contrato por colunas dedicadas ✓, features_snapshot ✓, outcome ✓, match de `profile_config_hash` normalizado em `profile_versions`). Requer aprovação manual (é escrita).
4. **H9 (hash):** normalizar `config_snapshot` antes de qualquer hash de versão (excluir `sl_pct` ATR-resolvido — o valor por-trade já vive em `sl_pct_applied`).
5. **H16 (observabilidade):** builder emitir tabela de descarte com reason codes (`excluded_by: outcome/features/valid_from/profile/lineage/contract/embargo`).
6. **Fase 17:** redefinir o readiness gate por lane com `effective_n` (nº de grupos de snapshot) + `positive_count/negative_count/profile_count/independent_days`, em vez de 2.800 linhas brutas de uma única lane.

## O que NÃO foi possível confirmar

- **Fase 16 (análise de sensibilidade A/B/C)**: exige treinos diagnósticos — fora do escopo read-only desta auditoria. É o próximo passo natural após o item 3 acima.
- Chaves exatas que distinguem hashes de config aparentemente idênticos na amostra V1 (diferem em chave não exibida — ttt/fee); requer diff par-a-par completo.
- Equivalência entre `md5(config_snapshot::text)` (SQL) e o hash canônico da aplicação (`snapshot_hash`/`config_hash` em `shadow_trade_service.py`) — o hash SQL desta auditoria é diagnóstico.
- Inventário completo de aliases/unidades (H14) por período — nenhum red flag nos ranges medidos, mas sem varredura exaustiva de chaves.
- 2 queries falharam por `could not resize shared memory segment ... No space left on device` no Postgres (transitório; re-executadas com `max_parallel_workers_per_gather=0` com sucesso) — **sinal operacional**: monitorar espaço/configuração de memória compartilhada do Postgres do Railway.
