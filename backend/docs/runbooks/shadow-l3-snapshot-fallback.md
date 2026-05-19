# Shadow Portfolio — fallback de resolução em 3 níveis (Task #303)

## Por que existe

`pipeline_scan._should_log_decision` grava em `decisions_log` **apenas em
transições** (BLOCK→ALLOW, ALLOW→BLOCK, ALLOW→ALLOW com delta de score > 5
ou flip de direção). Símbolos cronicamente aprovados em L3 com score estável
(ex.: BTC_USDT score 58 estável por dias) NUNCA geram nova linha — e a tela
"Currently Approved (L3)" do Decision Log mostrava esses símbolos enquanto
o Shadow Portfolio os ignorava silenciosamente.

A fonte canônica de "atualmente aprovado em L3" é
`pipeline_watchlist_assets` (mesma tabela que serve `/decisions/approved-snapshot`
e `/api/diagnostics/l3-queue`). O Shadow agora consulta esse snapshot e
faz cascata para encontrar uma "decisão" usável.

## Cascata implementada

Em `shadow_trade_service._resolve_decision_with_fallback` (chamada por
`safe_backfill_watchlist_shadows` a cada ciclo do `shadow_trade_monitor`):

| Ordem | source       | Critério                                                                                       |
|-------|--------------|------------------------------------------------------------------------------------------------|
| 1     | `recent_log` | `decisions_log` mais recente com ALLOW+SPOT dentro de `SHADOW_LOOKBACK_MINUTES` (default 10).  |
| 2     | `stale_log`  | Mesma query sem janela de tempo — pega a última ALLOW+SPOT histórica.                          |
| 3     | `live_l3`    | Constrói uma `_SyntheticDecision` (id=None) a partir do snapshot vivo de `pipeline_watchlist_assets`. |

Cada fonte que resolve um símbolo registra um `INFO` no log do monitor:

```
[shadow] backfill created id=<uuid> symbol=<SYM> decision_id=<int|None> source=<recent_log|stale_log|live_l3>
```

E incrementa o Counter Prometheus:

```
scalpyn_shadow_resolved_source_total{source="recent_log|stale_log|live_l3"}
```

## Diagnóstico

### Símbolo aparece em "Currently Approved (L3)" mas não no Shadow

1. Verificar se o símbolo está no snapshot vivo:
   ```sql
   SELECT pwa.symbol, pwa.alpha_score, pwa.refreshed_at, pw.market_mode, pwa.level_direction
     FROM pipeline_watchlist_assets pwa
     JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
    WHERE pw.user_id = '<uid>'
      AND UPPER(pw.level) = 'L3'
      AND LOWER(pw.market_mode) = 'spot'
      AND (pwa.level_direction IS NULL OR pwa.level_direction = 'up');
   ```
   Se o símbolo NÃO está na lista, o problema é upstream no `pipeline_scan`,
   não no Shadow. Investigar L1→L2→L3.

2. Verificar se já existe shadow RUNNING para o símbolo:
   ```sql
   SELECT id, status, entry_price, created_at
     FROM shadow_trades
    WHERE user_id = '<uid>' AND symbol = '<SYM>' AND status = 'RUNNING';
   ```
   Se sim, o backfill pulou (idempotência via `ux_shadow_running_user_symbol`).

3. Procurar a linha de criação no log do worker:
   ```
   [shadow] backfill created ... symbol=<SYM> ... source=<X>
   ```
   Se `source=live_l3`, a cascata caiu para o snapshot — esperado para
   símbolos cronicamente aprovados sem transição em `decisions_log`.

### Snapshot vivo está vazio

Pode acontecer se o `pipeline_scan` ainda não rodou desde o boot, ou se o
usuário não tem watchlist L3 spot ativa. Confirmar com:

```sql
SELECT COUNT(*)
  FROM pipeline_watchlists
 WHERE user_id = '<uid>' AND UPPER(level) = 'L3' AND LOWER(market_mode) = 'spot';
```

Zero → onboarding incompleto, criar watchlist L3.

### Métricas anormais

* `scalpyn_shadow_resolved_source_total{source="live_l3"}` crescendo muito
  rápido enquanto `recent_log`/`stale_log` ficam zerados ⇒ provável regressão
  no `pipeline_scan` parando de escrever transições. Cruzar com
  `decisions_log` (`SELECT COUNT(*) FROM decisions_log WHERE created_at > NOW() - INTERVAL '1 hour'`).

* `live_l3` zerado por dias enquanto a tela "Currently Approved (L3)" lista
  novos símbolos ⇒ o monitor não está executando. Verificar Celery beat
  schedule `app.tasks.shadow_trade_monitor.run` (default `SHADOW_MONITOR_INTERVAL_S=300`).

## Mudança de esquema

Migration `057_shadow_dec_id_null` relaxou `shadow_trades.decision_id` para
`NULL`able — necessário porque shadows sintéticas do `source=live_l3` não
têm `decisions_log.id` para apontar. ÚNICO caller que grava NULL é o
caminho `live_l3`; todos os outros (recent_log, stale_log, e os
`safe_create_*` legados) continuam apontando para uma DecisionLog real.

`shadow_trades.decision_id` NÃO está em `CRITICAL_COLUMNS` — não precisou
de rollout em duas fases.
