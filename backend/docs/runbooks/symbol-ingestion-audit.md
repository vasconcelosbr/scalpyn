# Runbook — Auditoria e reparo da ingestão de símbolos do pool

**Task de origem**: #194 — *Auditar e reparar em lote a ingestão de símbolos do pool*

Este runbook cobre o procedimento operacional para diagnosticar e
corrigir símbolos exibindo "SEM DADOS" (`taker_ratio` / `volume_delta`
nulos) em massa.

## Quando acionar

Use este runbook quando:

- A UI mostra ≥ 5 símbolos consecutivos com `taker_ratio` ou
  `volume_delta` em "SEM DADOS" durante mais de 10 minutos.
- O alerta `[WS-AUDIT CRITICAL]` aparece nos logs (qualquer ocorrência).
- O alerta `[POOL-AUDIT WARN]` aparece com contagem ≥ 10.
- Após uma alteração na tabela `pool_coins` (aprovação manual em massa,
  importação de novo asset universe, rollback de migração).

## Modelo mental

Cada símbolo cai em um (e apenas um) destes estados, em ordem de
gravidade:

| Status              | Causa                                                              | Reparo                                       |
| ------------------- | ------------------------------------------------------------------ | -------------------------------------------- |
| `NOT_APPROVED`      | `pool_coins.is_approved = false` (ou linha ausente).               | `UPDATE pool_coins SET is_approved = true`.  |
| `NOT_SUBSCRIBED`    | Aprovado, mas fora do universo SPOT do WS leader (drift).          | `gate_ws_leader.refresh_subscriptions()`.    |
| `NO_REDIS_DATA`     | Inscrito, porém `trades_buffer:spot:{symbol}` vazio.               | Esperar 3×2 s; se persistir → revisar WS.    |
| `NO_INDICATOR_DATA` | Buffer com dados, mas falta `taker_ratio` / `volume_delta` no row. | Enfileirar `compute_indicators.compute_5m`.  |
| `OK`                | Última linha microstructure tem ambas as chaves e idade < 15 min.  | Nada a fazer.                                |

## Etapa 1 — Diagnóstico inteiro (dry-run)

```bash
python -m scripts.symbol_health_audit --dry-run --json | tee /tmp/audit.json
```

A saída traz `report.counts` com a contagem por status e
`remediation.actions` com a lista de ações que **seriam** aplicadas.
Nenhuma escrita ocorre.

## Etapa 2 — Reparo em massa

```bash
# Reparo completo: aprova, refaz subscriptions, reexecuta indicadores.
python -m scripts.symbol_health_audit

# Variante segura: nunca aprova nada novo (somente refresh + recompute).
python -m scripts.symbol_health_audit --no-approve
```

O CLI valida cada `NOT_APPROVED` contra `GET /spot/currency_pairs` da
Gate.io antes de aprovar — se o símbolo não estiver tradable lá, ele é
ignorado (`skip_not_tradable_on_gate`).

## Etapa 3 — Endpoint admin (alternativa fora do shell)

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_DIAGNOSTICS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}' \
  https://<host>/api/admin/diagnostics/symbol-audit
```

Mesma payload do CLI; útil quando não há acesso shell ao Cloud Run.

## Etapa 4 — Verificação

Após o reparo, rode o endpoint diagnóstico por símbolo para confirmar:

```bash
curl -H "Authorization: Bearer $ADMIN_DIAGNOSTICS_TOKEN" \
  https://<host>/api/admin/symbol-health/BTC_USDT | jq
```

Os campos `trade_buffer.exists=true`, `live_probes.order_flow_300s.taker_ratio`
e `indicators_history.latest_per_scheduler_group[*].has_taker_ratio=true`
devem estar todos populados em até 1 ciclo (≤ 5 min).

## Etapa 5 — Monitoramento contínuo

A tarefa `app.tasks.symbol_health_audit.monitor_only` roda a cada 5 min
(beat schedule) e emite os alertas:

- `[POOL-AUDIT WARN]` — dedup 10 min.
- `[WS-AUDIT CRITICAL]` — dedup 10 min.
- `[REDIS-FALLBACK INFO]` — dedup 5 min.

Para promover o monitor a reparo automático em produção, defina
`SYMBOL_AUDIT_REPAIR=1` no Cloud Run. Recomenda-se manter em
`monitor_only` enquanto o operador valida o comportamento por algumas
horas.

## Falhas conhecidas

### "todos os símbolos saem como NOT_APPROVED"

→ Migração 035 (`pool_coins.is_approved`) não aplicada. Verifique:

```bash
curl https://<host>/api/health/schema | jq
```

Se a coluna estiver ausente, rode `alembic upgrade head` na main app
antes de tentar o reparo. O classificador degrada graciosamente nesse
caso (sem 500), mas nenhum reparo será efetivo até a coluna existir.

### "refresh_subscriptions=true mas nada mudou"

→ A instância Cloud Run que recebeu o request pode não ser a leader. O
`refresh_subscriptions()` grava uma flag em Redis (`gate_ws:refresh_request`);
o leader ativo a observa em até `LEADER_RENEW_INTERVAL_SECONDS` (10 s) e
reinicia o WS. Confira o log da instância leader:

```
[gate-ws-leader] refresh_subscriptions requested — restarting WS
```

### "GateSymbolValidator refresh failed"

→ A Gate.io não respondeu. O validador é fail-open (assume tradable),
para não bloquear o reparo durante outage curto. Para auditorias de
massa, prefira rodar o CLI quando a Gate estiver saudável.
