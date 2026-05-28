# EXECUÇÃO — FASE 0 + GUARDRAILS (Scalpyn Auto-Pilot / ML)

> Continuação da auditoria já realizada. Esta fase EXECUTA mudanças, mas com sequência
> de segurança rígida: **nada de escrita autônoma real até validação em dry-run**.
> Profile alvo da autonomia: **L3 ID `29155eda-6d8f-4abf-9f58-b3999ba9c878`**.

---

## REGRAS ABSOLUTAS (inalteradas)
- Toda mudança é **ADITIVA**. Não remover/alterar código, rotas, tabelas, componentes ou
  dados existentes — apenas estender.
- **VALIDE o código real antes de editar.** Leia o arquivo; não presuma por nome.
- Caminhos de arquivo **explícitos** em tudo.
- Toda config nova vive em **config_profiles / JSONB** — Zero Hardcode. Nenhum threshold
  novo via env var ou literal no código.
- Antes de cada edit, mostre o trecho atual e o trecho proposto. Não faça edits em massa
  silenciosos.

## CONTEXTO HERDADO DA AUDITORIA (fatos confirmados)
- `ML_GATE_ENABLED=false` (`pipeline_scan.py:2764`) → modelo v15 NÃO filtra trades. O ML
  está inerte. **Não é urgência desta fase.**
- Sangria real e ativa: **EV = -0.1125% por trade** (~-11 bps), vindo das regras de
  score + entry triggers, não do ML.
- `EV_MIN_THRESHOLD = -0.30` (`autopilot_engine.py:48`) está correto como código, mas
  mal calibrado → autopilot nunca muta apesar do sistema perder dinheiro.
- `rejected_count=0` no autopilot → nenhum shadow trade de trade REJEITADO tem outcome.
  O autopilot decide com base só nos aprovados (viés de seleção).
- Market Data Hub: campos macro são TRUNCADOS na inferência e o modelo nem usa. Macro
  está inerte. Mas deve ser GRAVADO já, para acumular histórico.

---

## SEQUÊNCIA DE EXECUÇÃO (ORDEM É OBRIGATÓRIA — NÃO REORDENAR)

### PASSO 1 — VERIFICAÇÃO PASSIVA: gravação do macro snapshot
**Objetivo:** garantir que os campos macro estão sendo persistidos em
`decisions_log.metrics` a cada decisão AGORA, para acumular histórico para retreino
futuro. Não usa macro para decidir nada — só garante que o dado não se perca.

- Verifique se o `metrics` enriquecido (`{**metrics, **macro}`,
  `prediction_service.py:84`) é de fato o que chega a `decisions_log.metrics`, ou se o
  insert usa o `metrics` original (sem macro).
- Confirme com query: dos registros de decisão das últimas 48h, quantos têm os 15 campos
  macro preenchidos (não-NaN)? Reporte a contagem.
- Se o macro NÃO estiver sendo gravado: corrija o insert para persistir o dict
  enriquecido. **Mudança aditiva** — apenas garantir que campos macro entram no JSONB.
- **NÃO** remova a truncação de inferência, **NÃO** ligue ML gate, **NÃO** retreine.
  Isso é só plantio de dado.
- Entregável: contagem de cobertura macro + diff do fix (se necessário).

### PASSO 2 — INVESTIGAR `rejected_count=0` (BLOQUEADOR de autonomia)
**Objetivo:** entender por que nenhum shadow trade rejeitado tem outcome ANTES de dar ao
autopilot poder de inserir block_rules. Block_rules afetam quais trades são rejeitados —
dar esse poder enquanto o autopilot é cego para o resultado dos rejeitados cria um loop
de feedback sobre dados que ele não mede.

- Leia `shadow_trade_service.py` **integralmente** (não foi lido na auditoria).
- Responda com evidência:
  - Shadow trades de trades L3_REJECTED são criados? Eles recebem outcome (tp/sl) quando
    o preço evolui, ou ficam abertos para sempre?
  - Por que `rejected_count=0` em `autopilot_engine.py:101-113`? Os rejected nunca
    fecham, nunca são criados, ou são criados mas o autopilot não os lê?
  - A lógica de feature engineering + timing do shadow é IDÊNTICA à do path real?
    Aponte divergências linha a linha.
- Entregável: diagnóstico do rejected_count=0 (FATO, não hipótese) + se a paridade
  shadow/real está intacta. **Se a paridade estiver quebrada, isso vira bloqueador
  adicional e deve ser reportado antes de prosseguir.**

### PASSO 3 — GUARDRAILS EM CONFIG (sem ativar escrita ainda)
**Objetivo:** criar toda a infraestrutura de segurança ANTES de mexer no EV gate, com
`dry_run_mode=true` por padrão.

Criar registro em `config_profiles` com `config_type='autopilot_guardrails'`:
```json
{
  "ev_min_threshold_pct": 0.0,
  "fpr_max_threshold": 0.65,
  "selection_inversion_delta_pct": 0.50,
  "rule_max_delta_per_cycle": 1,
  "rule_points_min": -10,
  "rule_points_max": 10,
  "weight_max_delta_per_cycle": 5,
  "threshold_max_delta_per_cycle": 2,
  "min_samples_per_rule": 15,
  "circuit_breaker_threshold": 3,
  "circuit_breaker_pause_hours": 168,
  "kill_switch": false,
  "dry_run_mode": true,
  "scope_profile_id": "29155eda-6d8f-4abf-9f58-b3999ba9c878"
}
```
Implementar em `autopilot_engine.py` (aditivo):
- **3a — Kill-switch:** primeira linha de `run_autopilot_cycle()` lê `kill_switch`; se
  true → retorna `{"action": "KILLED"}`, não executa nada.
- **3b — Dry-run:** quando `dry_run_mode=true`, executar TODO o cálculo (gerar
  `new_config` e `adjusted_rules`), logar como `DRY_RUN_ANALYZED` /
  `DRY_RUN_MUTATED` / `DRY_RUN_RULES_ADJUSTED`, e **NÃO persistir** config/scoring_rules.
- **3c — Leitura dos limites:** todos os deltas/bounds vêm do JSONB acima, não de
  literais. Substituir o `EV_MIN_THRESHOLD = -0.30` hardcoded pela leitura de
  `ev_min_threshold_pct` do config (mantendo fallback seguro se o config não existir).
- **3d — Escopo travado:** validar que qualquer escrita só atinge `scope_profile_id`.
  Qualquer tentativa fora do escopo → log `SCOPE_VIOLATION_BLOCKED`, não executa.
- Entregável: diffs aditivos + confirmação de que com `dry_run_mode=true` nada é
  persistido.

### PASSO 4 — ATIVAR DRY-RUN E OBSERVAR (sem código — operação)
**Objetivo:** rodar o autopilot em dry-run com o EV gate já em 0.0% (lido do JSONB),
para ver o que ELE FARIA, sem escrever.

- Com `ev_min_threshold_pct=0.0` e `dry_run_mode=true`, o autopilot agora deve detectar
  que o sistema viola o gate (EV -0.11% < 0.0%) e PROPOR mutações.
- Logar cada mutação proposta com before/after completo (expandir `autopilot_audit_logs`
  para incluir `rules_changed: [{rule_id, indicator, operator, points_before,
  points_after, edge, n_samples}]` e `ev_before`/`ev_after` projetado).
- **Período de observação: ≥ 2 semanas.** Critérios para considerar o dry-run validado:
  - As mutações propostas são coerentes (não swings absurdos, respeitam os deltas).
  - `rejected_count` deixou de ser 0 (Passo 2 resolvido) OU está documentado por que
    permanece 0 e por que isso é aceitável.
  - Nenhum `SCOPE_VIOLATION_BLOCKED`.
- Entregável: relatório do que o autopilot proporia (não executado).

### PASSO 5 — RSI entry gate (mudança via API, independente do autopilot)
**Objetivo:** estancar parte da sangria imediatamente, sem depender do autopilot.
- Reduzir `entry_triggers` do profile L3 de `rsi < 80` para `rsi < 70` (recomendação
  histórica RSI_MAX=70).
- Via API `/api/profiles/{id}` — não requer mudança de código.
- **Por que separado:** isto ataca a sangria ativa diretamente e é reversível. Pode ser
  feito em paralelo ao período de observação do Passo 4.
- Entregável: confirmação do trigger alterado + (se possível) comparação de quantos
  trades recentes teriam sido bloqueados com rsi entre 70-80.

---

## O QUE **NÃO** FAZER NESTA FASE
- NÃO ligar `ML_GATE_ENABLED` (modelo v15 é inválido — Fase 1 futura).
- NÃO retreinar o modelo (sem dados suficientes — Fase 1).
- NÃO remover truncação de macro na inferência (Fase 3).
- NÃO ativar escrita real do autopilot (`dry_run_mode=false`) até Passos 2 e 4 validados.
- NÃO tocar em outros profiles, filtros de Pool/L1/L2, ou decision_threshold do ML.

## CRITÉRIO DE SAÍDA DA FASE 0 (gate para ativar escrita real)
Só desligar `dry_run_mode` quando TODOS forem verdadeiros:
1. Passo 2 resolvido — `rejected_count=0` explicado e paridade shadow/real confirmada.
2. Passo 4 — ≥ 2 semanas de dry-run com mutações coerentes e zero violações de escopo.
3. EV gate lendo de JSONB (`ev_min_threshold_pct=0.0`), kill-switch testado, auditoria de
   mutação completa funcionando.
4. RSI gate (Passo 5) aplicado e seu efeito na sangria medido.

Quando os 4 forem verdadeiros, propor (NÃO executar automaticamente) a ativação de
escrita real como passo separado, para aprovação humana explícita.
