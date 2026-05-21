# `decisions_log` — semântica e contratos

> Runbook consolidando lições da investigação do gap de 48h em
> `decisions_log` (2026-05-19 → 2026-05-21). Ver commits `02e5aec`,
> `ff2f8087`, `4b1922ef`.

## 1. `decisions_log` é audit trail de TRANSIÇÕES — NÃO snapshot por ciclo

**Regra:** uma row em `decisions_log` representa um **evento de mudança**
(símbolo qualificou, perdeu sinal, mudou direção, score variou
significativamente). Símbolo estável em L3 ALLOW **não** gera row a cada
scan. O frontend lê `pipeline_watchlist_assets` para saber "o que está
visível agora"; `decisions_log` é o histórico de transições.

**Tipos canônicos de `event_type`:**

- `NEW_SIGNAL` — primeira aparição em L3 ALLOW (gerado por `_should_log_decision`)
- `L3_VISIBLE` — fallback edge-triggered (1º cycle em que o símbolo aparece no set L3, caso `NEW_SIGNAL` não tenha sido emitido)
- `SIGNAL_LOST` — saiu de L3 ALLOW
- `SIGNAL_REGAINED` — voltou para L3 ALLOW após perda
- `SIGNAL_EVOLVED_*` — variações de score/direction acima dos thresholds

**Implementação:** `pipeline_scan.py::_evaluate_l3_decisions` (~linha
2720+) chama `_should_log_decision` para detectar transições. O fallback
`L3_VISIBLE` é guardado por `sym not in prior_visibility` — **NÃO
remover essa condição** (foi a regressão que tentamos e revertemos no
commit `4b1922ef`).

**Anti-padrão (não fazer):** forçar row por ciclo para garantir
"observabilidade". Para isso existe `pipeline_watchlist_assets`,
metrics Prometheus e o painel `/dashboard/performance`. Encher
`decisions_log` polui o dataset do ML e cria barulho na UI
`/decisions`.

## 2. Contrato de `_replace_rejection_snapshot` — 5 chaves obrigatórias

`pipeline_scan.py::_replace_rejection_snapshot` itera o snapshot de
rejeições e quebra com `KeyError` se faltar qualquer uma das chaves:

```python
{
    "symbol": str,
    "failed_type": str,        # ex.: "gate_min_alpha_score"
    "failed_indicator": str,   # ex.: "alpha_score"
    "condition": str,          # ex.: ">= 55"
    "current_value": Any,      # ex.: 42.3
    "expected": Any,           # ex.: 55
}
```

**Causa raiz do gap de 48h:** o append inline em `pipeline_scan.py:2585`
(branch do gate `min_alpha_score`) montava o dict só com `symbol` +
`reason`. A exceção era engolida pelo try/except por-watchlist e o L3
abortava antes de chamar `_evaluate_l3_decisions` → zero rows no
`decisions_log`. Fix em commit `02e5aec`.

**Como detectar regressão futura:** se ver `errors: 1` (ou similar) nos
logs do worker-structural durante `pipeline_scan` sem causa óbvia,
greppar por `KeyError` ou logar o snapshot antes do `_replace_*`.
Qualquer novo callsite que appenda em `rejection_snapshot` precisa
emitir o dict completo. Considerar mover para uma factory function
(`make_rejection_entry(...)`) se aparecer um terceiro callsite.

## 3. `_save_l3_visibility` faz sliding TTL — cuidado ao usar isoladamente

`pipeline_scan.py::_save_l3_visibility` (~linha 462) faz:

```python
pipe.delete(key)
pipe.sadd(key, *symbols)
pipe.expire(key, ttl)  # ttl = 86400 (24h)
```

Isso **resetta o TTL a cada scan**, ou seja: enquanto um símbolo
continuar em L3 ALLOW, ele fica no set para sempre — o TTL de 24h **só
expira se o símbolo sair do conjunto e nenhum scan re-adicioná-lo
dentro de 24h**.

**Implicação:** qualquer guard do tipo `sym not in prior_visibility`
funciona como "primeira vez que esse símbolo apareceu desde que
o pipeline tem memória" — não como "primeira vez nas últimas 24h".
Combinado com `_should_log_decision` retornando `(False, None)` para
ALLOW estável com `|Δscore| ≤ 5`, isso é **comportamento desejado**
(ver item 1).

**NÃO** trocar para `pipe.sadd(...)` sem o `delete` + `expire`: sem o
`delete`, símbolos que saem do L3 ficariam no set indefinidamente; sem
o `expire`, a chave nunca seria limpa se o pipeline parasse. O design
atual é correto, só precisa estar documentado.

---

## Apêndice — Linha do tempo do incidente

| Data | Evento |
|---|---|
| 19/05 ~XX:XX | Primeiro `KeyError 'failed_type'` durante scan, L3 começa a abortar silenciosamente |
| 19/05 → 21/05 | `decisions_log` para de receber rows; `pipeline_watchlist_assets` continua atualizando normalmente (SUI/PI visíveis na UI) |
| 21/05 | User reporta gap; investigação via SQL prod confirma SUI(68.40)/PI(63.20) em `pipeline_watchlist_assets` mas zero rows em `decisions_log` |
| 21/05 ~16:50Z | Commit `02e5aec` em prod (fix KeyError) — Camada 1 |
| 21/05 | Tentativa de fix Camada 2 (`ff2f8087`) — força L3_VISIBLE por ciclo + bypass dedup |
| 21/05 | Revert Camada 2 (`4b1922ef`) — user esclarece que comportamento edge-triggered é o desejado |
| 21/05 | Remoção do `Motivo do skip` da UI (`b391ebc5`) e NULL em `shadow_trades.skip_reason` (`faaa1d79`) — anti-leak XGBoost |
