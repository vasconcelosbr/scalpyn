# Fix Pós-Auditoria ML Shadow Trades — 2026-06-10

**Executor:** Claude Sonnet 4.6  
**Data:** 2026-06-10  
**Status:** PARTE 0 = FAIL → **execução interrompida conforme protocolo**

---

## PARTE 0 — Verificação da Semântica do Source

> **VEREDITO: FAIL em todos os três itens.**  
> As Partes 1–3 NÃO foram aplicadas. Diagnóstico abaixo.

---

### 0.1 — Verificação de Código (FAIL)

**Arquivo:** `backend/app/tasks/pipeline_scan.py:3093–3103`

```python
# linha 3093-3103
_allow_decision_ids = [
    p["id"] for p in decision_payloads
    if p.get("decision") == "ALLOW" and p.get("id")   # ← condicional explícita
]
if _allow_decision_ids:
    from ..services.shadow_trade_service import (
        create_shadows_for_new_decisions,
    )
    await create_shadows_for_new_decisions(
        wl.user_id, _allow_decision_ids
    )
```

A criação de shadow trade ocorre **dentro de um `if decision == "ALLOW"`**. A promoção L1 não é o trigger — o trigger é o L3-ALLOW. Há também um segundo caminho (`pipeline_scan.py:3160`) para shadows de decisões bloqueadas apenas pelo score gate (`SHADOW_BYPASS_SCORE_GATE=true`), mas esse env var está desabilitado por padrão.

**Resposta à pergunta do prompt:**
- A criação ocorre **depois** de toda avaliação de filtro, score threshold e block rules — no final do funil L1→L2→L3.
- Existe condicional de qualidade: `if p.get("decision") == "ALLOW"`.

---

### 0.2 — Teste do Rejeitado (FAIL)

```sql
SELECT COUNT(*) AS shadows_de_rejeitados
FROM shadow_trades st
JOIN decisions_log dl ON dl.id = st.decision_id
WHERE st.created_at > '2026-06-10T03:52:00Z'
  AND dl.decision != 'ALLOW';
-- shadows_de_rejeitados: 0
```

Também relevante: `decisions_log` contém **apenas** `decision='ALLOW'` (1588 registros pós-V2, todos ALLOW). A tabela `decisions_log` não registra decisões REJECT/BLOCK — ela é um log de promoções, não de avaliações.

---

### 0.3 — Reconciliação de Funil (FAIL)

```
Hora (UTC) | decisions_log | shadows | Razão
04:00      | 179           | 21      | 11.7%
05:00      | 182           | 21      | 11.5%
06:00      | 169           | 27      | 16.0%
07:00      | 171           | 26      | 15.2%
08:00      | 178           | 29      | 16.3%
```

Razão média: ~14%. Muito abaixo dos ~100% esperados se a captura fosse L1 pré-filtro.

---

## Diagnóstico da Arquitetura de Captura

### O que realmente acontece

```
Universo de ativos (~100 símbolos)
  ↓ L1 filter
  ↓ L2 filter
  ↓ L3 score gate + block rules
  → ALLOW: logado em decisions_log (source=L3)
      ↓ ux_shadow_running_user_symbol unique constraint
        (bloqueia novo shadow se o símbolo já tem shadow RUNNING)
      → ~14% dos ALLOWs viram novos shadows
  → REJECT/BLOCK: NÃO logado em decisions_log (invisível)
```

### Duas camadas de censura

**Camada 1 — L3 gate:** apenas L3-ALLOWs chegam a `create_shadows_for_new_decisions`. O pipeline não registra decisões rejeitadas. A `decisions_log` é um log de promoções, não de avaliações.

**Camada 2 — Unique constraint de symbol:** `ux_shadow_running_user_symbol` (`shadow_trades`) impede criar novo shadow para um símbolo que já tem shadow RUNNING. Isso explica a razão de ~14%: a maioria dos ALLOWs é para símbolos que já têm shadow ativo e são descartados silenciosamente.

### O que o ML aprende com essa estrutura

O trainer recebe:
- Trades que passaram pelo funil completo L1→L2→L3 **E**
- Foram a PRIMEIRA entrada no símbolo desde que o shadow anterior fechou

O modelo treina para prever WIN_FAST **dentro da subpopulação de sinais L3-aprovados sem shadow running**. É uma tarefa válida, mas com dois bias embutidos:
1. **Viés de sobrevivência de sinal:** nunca vê os sinais que o L3 rejeitou.
2. **Viés de cooldown de símbolo:** tende a capturar entradas em ruptura (primeiro sinal após período quieto) em vez de sinais em símbolos com momentum contínuo.

### Por que `source='L3'` é semanticamente correto

O rótulo `'L3'` reflete fielmente o ponto de captura: L3-ALLOW. Renomear para `'WATCHLIST_SPOT'` (como o prompt propunha) seria semanticamente incorreto — implicaria captura de toda a watchlist, sem filtros, o que não é verdade.

### O `SHADOW_BYPASS_SCORE_GATE` (desabilitado)

`pipeline_scan.py:3119-3184` implementa um caminho alternativo: quando `SHADOW_BYPASS_SCORE_GATE=true`, avalia os assets rejeitados **apenas** pelo score gate e cria shadows para os que passariam o L3. Isso captura a margem do score gate, mas ainda é pós-L1/L2 e pós-block rules. Não é uma captura L1 plena.

---

## Impacto sobre o Smoke Train

As FAILs B1/B2/B3 da auditoria (net_return_pct, MAE sign, schema drift) são bugs de instrumentação independentes da questão de censura. No entanto, conforme o protocolo do prompt, as Partes 1–3 não foram aplicadas.

### O que PODE ser feito agora sem violar o protocolo

Nenhuma mudança de código. O diagnóstico é o entregável desta execução.

---

## Questões para o Operador (Próximo Prompt)

Antes de liberar as correções B1/B2/B3, é necessária uma decisão de arquitetura:

**Opção A — Aceitar a captura L3 atual como intencional**
> O ML otimiza dentro da subpopulação L3-aprovada. `source='L3'` permanece como rótulo correto. Aplicar B1/B2/B3 sem mudar o ponto de captura. Documentar o viés de sobrevivência em `docs/ML_PIPELINE.md`.

**Opção B — Expandir a captura para incluir L3-rejected**
> Ativar `SHADOW_BYPASS_SCORE_GATE=true` para capturar decisions rejeitadas apenas pelo score threshold (mantendo L2 filter e block rules). Requer: env var no Railway + análise do impacto em volume (potencialmente 5-10× mais shadows). Ainda é censura parcial (L1/L2/block), não L1 puro.

**Opção C — Captura pré-filtro na L1 (cirurgia no pipeline)**
> Criar `shadow_trade_monitor_l1.py` que captura todos os símbolos promovidos na L1 e os acompanha até outcome. Requer: novo ponto de inserção no pipeline, nova fila Celery, novo worker. Volume seria ~1000× maior que hoje. Esta é a opção mais informativa para o ML, mas é a maior mudança arquitetural.

**Recomendação:** Opção A no curto prazo (aceitar o bias, documentar, aplicar B1/B2/B3) + avaliar Opção B como experimento (SHADOW_BYPASS_SCORE_GATE) assim que o dataset pós-B1/B2/B3 acumular ≥500 trades com instrumentação limpa.

---

## Checklist Pós-Decisão

Após o operador decidir entre Opções A/B/C:

- [ ] **Se A:** gerar novo prompt aplicando apenas B1/B2/B3 + documentação do bias
- [ ] **Se B:** gerar prompt para ativar SHADOW_BYPASS_SCORE_GATE + env var no Railway + análise de volume
- [ ] **Se C:** gerar prompt de arquitetura para o novo capture point (não acompanha bugfixes)

---

*Gerado por Claude Sonnet 4.6 — Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>*
