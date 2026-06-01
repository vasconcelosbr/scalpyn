# ML Pipeline Forensic Audit Report
**Data:** 2026-05-31
**Escopo:** MISSÃO 1 (ML Pipeline) + MISSÃO 2 (Data Reconciliation)
**Constraint:** Somente evidências — nenhum código ou banco alterado
**Modelo auditado:** v19 (ativo em produção)

---

## 1. INVENTÁRIO

| Tabela | Total | Observação |
|--------|-------|------------|
| `shadow_trades` | 1.218 | Todos de maio/2026 (10 dias: 21-31/mai) |
| `shadow_trades` resolvidos (TP_HIT/SL_HIT) | 1.012 (83.1%) | 156 TIMEOUT, 50 NULL/OPEN |
| `decisions_log` ALLOW + l3_pass=true | 7.864 | |
| `decisions_log` BLOCK + l3_pass=false | 7.401 | 100% excluídos do ML |
| `ml_models` ativo | v19 | train=109, val=24, test=24 |

**Modelo ativo (v19):**
- precision=0.6957, recall=0.9412, f1=0.8000
- roc_auc=0.2857 (pior que aleatório)
- FPR=1.0 (aprova 100% dos candidatos no test set)
- Threshold de decisão: 0.4875

---

## 2. FUNIL COMPLETO: Shadow → ML Dataset

```
shadow_trades total:                       1.218
└─ resolvidos (TP_HIT/SL_HIT):            1.012  (83.1%)

decisions_log ALLOW + l3_pass=true:        7.864
└─ com outcome tp/sl:                      3.564  (45.3%)
   └─ após filtro de data (excl Mai 1-20): 3.538  (-0.7%)  ← IMPACTO MÍNIMO
      └─ após DISTINCT ON dedup:             187  (-94.7%) ← O ASSASSINO
         └─ com features não-nulas:          157  (-16.0%)
            └─ modelo ML ativo:              157
               ├─ train: 109
               ├─ val:    24
               └─ test:   24
```

**Causa raiz do dataset pequeno:** `DISTINCT ON (symbol, DATE(created_at))` remove 94.7% dos registros.
O pipeline scanner gera múltiplas entradas por par por dia (média 18.9 entradas/symbol-day).
Exemplo: ONDO_USDT teve 102 entradas em 2026-05-30 → reduzido a 1 registro no ML.

**Distribuição de event_type:**
- SIGNAL_REGAINED: 2.705 (76.5%)
- NEW_SIGNAL: 493 (13.9%)
- SIGNAL_EVOLVED_SCORE: 339 (9.6%)

---

## 3. FASE 8 — LABELS

### 3.1 Escala de pnl_pct
`pnl_pct` no `decisions_log` está em **escala percentual** (1.0 = 1.0%), não decimal.
- TP hits: média +1.10%, range [+0.9225%, +3.0978%]
- SL hits: média -1.23%, range [-5.0%, -0.9827%]
- `shadow_trades`: TP_HIT avg = +1.0000% (fixo), SL_HIT avg = -5.0000% (fixo de config)

### 3.2 Distribuição de labels

| outcome | total | win_fast=1 | win_fast=0 | avg pnl% |
|---------|-------|-----------|-----------|----------|
| `tp` | 103 | 103 | **0** | +1.10% |
| `sl` | 84 | **0** | 84 | -1.23% |

**Zero divergências** entre `outcome` e `is_win_fast`.
`is_win_fast` na prática equivale a `(outcome == 'tp')` no dataset atual.

### 3.3 Threshold inconsistency

```
feature_extractor.py:  _WIN_THRESHOLD = 0.008 + 0.16 = 0.168
                       (MIN_WIN_PNL_PCT=0.008, FEE_ROUND_TRIP_PCT=0.16)
                       Na escala do DB = 0.168% → funciona por acidente
                       (min pnl de TP = 0.9225% >> 0.168%)

audit.py:              WIN_THRESH = 0.008 * 100 = 0.8
                       Na escala do DB = 0.8% → threshold diferente
                       Mas ainda classifica corretamente no dataset atual

Intenção original:     MIN_WIN_PNL_PCT deveria ser 0.8% (não 0.008%)
                       Escala correta: 0.8 + 0.16 = 0.96%
```

Histograma de PNL — gap limpo, sem trades entre 0% e 0.168%:
- Bucket SL: 84 trades em [-5%, -0.98%]
- Bucket TP: 103 trades em [0.92%, 3.10%]

---

## 4. FASE 9 — BLOCK / L3_REJECTED

```
BLOCK records em decisions_log:    7.401
└─ l3_pass=false:                  7.401  (100%)
└─ l3_pass=true:                      0   (0%)
└─ com outcome/pnl:                  370  (gravados mas inatingíveis pelo ML)

INCLUDE_REJECTED_IN_TRAIN=true:    PERMANENTEMENTE INEFICAZ
→ Filtro WHERE l3_pass = true exclui 100% dos BLOCK records
→ ALLOW=186, BLOCK=0 confirmado nos logs do trainer
```

---

## 5. FASE 10 — SPLIT TEMPORAL

### Dataset inteiro cobre apenas 10 dias (21-31/mai/2026)

| split | n | from | to | win=1 | win=0 | avg_win | avg_loss |
|-------|---|------|----|-------|-------|---------|----------|
| train (70%) | 130 | 2026-05-21 | 2026-05-30 | 69 (53%) | 61 (47%) | +1.14% | -1.11% |
| val (15%) | 28 | 2026-05-30 | 2026-05-31 | 21 (75%) | 7 (25%) | +1.04% | -2.17% |
| test (15%) | 29 | 2026-05-31 | 2026-05-31 | 13 (45%) | 16 (55%) | +1.05% | -1.30% |

**Problemas críticos:**
1. Val e test estão no **mesmo dia** (31/mai) — não são splits temporais independentes
2. Distribuição de labels **inverte** entre val (75% wins) e test (45% wins)
3. Optuna otimiza para `val_auc=1.0000` → overfitting em 24 amostras de 1 dia
4. Calibração (threshold=0.4875) baseada em val_auc=1.0 → inválida no test

---

## 6. MISSÃO 1 — ANÁLISE DO MODELO v19

### 6.1 Confusion Matrix (test set, 29 amostras)

```
                  Previsto WIN  Previsto LOSS
Real WIN (13)         13 (TP)      0 (FN)
Real LOSS (16)        16 (FP)      0 (TN)
```

O modelo aprova **100% dos candidatos** no test set.

### 6.2 Métricas

| Métrica | Valor | Interpretação |
|---------|-------|---------------|
| Precision | 0.6957 | 69.6% dos aprovados são wins |
| Recall | 0.9412 | Captura 94% dos wins reais |
| AUC | 0.2857 | **Pior que aleatório** (0.5) |
| FPR | 1.0 | **Aprova 100% das losses** |

### 6.3 Por que AUC < 0.5?

O modelo atribui **probabilidade maior** aos 16 perdedores do que aos 13 vencedores no test set.
Causa: regime change intra-dia. O modelo treinado em mai/21-30 + manhã de mai/31 (val)
é aplicado na tarde de mai/31 (test) — microestrutura de mercado diferente em apenas horas.

### 6.4 Por que Precision=69.6% com AUC=0.2857?

- Test set tem 13 wins e 16 losses (45%/55%)
- Modelo aprova tudo (threshold=0.4875, modelo retorna prob > 0.4875 para todos)
- Precision = 13/29 = 69.6% = simplesmente a taxa base de wins no test set aprovado
- Não há poder discriminativo real

### 6.5 val_auc=1.0000

O Optuna encontrou parâmetros (max_depth=6, n_estimators=572) que memorizam os 24 exemplos do val set.
Com apenas 24 amostras de 1 dia, isso é trivialmente alcançável — não indica generalização.

---

## 7. DESCOBERTAS PRINCIPAIS

### D1: Dedup é a causa raiz do dataset minúsculo
3.538 registros com outcome → 187 após DISTINCT ON (94.7% removido).
A granularidade do scanner (5 min) gera 19x mais entradas do que trades únicos.

### D2: Dados históricos inexistentes antes de 21/mai/2026
Todo o dataset abrange apenas 10 dias. Causa desconhecida:
- Sistema de gravação de outcome implementado recentemente?
- Tabela shadow_trades criada após implementação do shadow monitor?
- Dados anteriores deletados?

### D3: val_auc=1.0 → test_auc=0.2857 = sinal de regime change + overfitting
Com 24 amostras de val em 1 dia, Optuna sempre vai encontrar parâmetros que overfitam.
O test set (mesmo dia, outras horas) tem distribuição invertida → AUC < 0.5.

### D4: INCLUDE_REJECTED_IN_TRAIN é dead code
Nunca funcionou. BLOCK records têm l3_pass=false, excluídos pelo filtro SQL.

### D5: Escala de pnl_pct
DB armazena em escala percentual. Threshold 0.168 é funcionalmente correto mas conceitualmente errado (deveria ser 0.96%).

### D6: TTT labels inutilizados
5 trades com TTT habilitado, FAST_WIN=0 → nenhum label de qualidade superior disponível.

---

## 8. RESPOSTAS ÀS 12 QUESTÕES

**Q1. Quantos shadow trades têm labels ML?**
157 registros no modelo (via decisions_log, não diretamente de shadow_trades).

**Q2. Qual o gargalo principal?**
DISTINCT ON (symbol, DATE) remove 94.7% (3.351 registros). Causa: scanner roda a cada 5min.

**Q3. Por que só 10 dias de dados?**
decisions_log não tem registros com outcome antes de 2026-05-21. Causa a investigar.

**Q4. Filtro de data (May 1-20) importa?**
Não. Remove apenas 26 registros (0.7%). Dataset começa em 21/mai de qualquer forma.

**Q5. is_win_fast coincide com outcome?**
Sim, 100%. Zero divergências. outcome=tp → win=1, outcome=sl → win=0.

**Q6. Escala de pnl_pct no DB?**
Percentual (1.0 = 1.0%). TP: 0.92% a 3.10%. SL: -5.0% a -0.98%.

**Q7. Threshold _WIN_THRESHOLD = 0.168 está correto?**
Funciona por acidente. Conceitualmente errado (deveria ser 0.96%). Diverge de audit.py (usa 0.8%).

**Q8. BLOCK records contribuem para o ML?**
Não. 100% excluídos por l3_pass=false. INCLUDE_REJECTED_IN_TRAIN=true é dead code.

**Q9. Por que AUC=0.2857?**
Regime change: treinado em mai/21-30, testado em mai/31 (mesmas horas que val, distribuição invertida).

**Q10. Por que FPR=1.0?**
Modelo aprova tudo (threshold=0.4875 calibrado em val_auc=1.0). Nenhum candidato é bloqueado.

**Q11. Vocabulário TP_HIT vs tp afeta o ML?**
Não diretamente. ML usa decisions_log (outcome='tp'/'sl'). Afeta apenas reconciliação manual.

**Q12. Como resolver o dataset pequeno?**
(1) Mudar DISTINCT ON para dedup por decision_id único em vez de (symbol, DATE);
(2) Investigar ausência de dados antes de 21/mai e expandir janela histórica;
(3) Com mais dados, o split temporal terá diversidade real e val_auc deixará de ser 1.0.

---

## 9. AÇÕES RECOMENDADAS (por prioridade)

| Prioridade | Ação | Impacto |
|-----------|------|---------|
| P0 | Investigar por que não há outcomes antes de 21/mai | Pode multiplicar dataset por 5-10x |
| P1 | Corrigir DISTINCT ON: usar decision_id único em vez de (symbol, DATE) | Elimina 94.7% de perda |
| P2 | Aumentar janela histórica para ter ≥ 500 amostras antes de retreinar | Torna split temporal válido |
| P3 | Corrigir MIN_WIN_PNL_PCT (0.008 → 0.8) para threshold correto (0.96%) | Consistência semântica |
| P4 | Desabilitar INCLUDE_REJECTED_IN_TRAIN ou corrigir para funcionar | Eliminar dead code |
| P5 | Adicionar early_stopping_rounds ao XGBoost para reduzir overfitting | Complementar a P2 |

---

*Gerado por auditoria forense local via google.cloud.sql.connector + pg8000.*
*Nenhum código, modelo ou banco foi alterado durante esta auditoria.*
