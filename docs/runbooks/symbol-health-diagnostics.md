# Runbook — Symbol Health Diagnostics

## When to use

A user reports an asset (e.g. `DOGE_USDT`) stuck on **"SEM DADOS /
aguardando coleta"** in the watchlist Block Rules for one or more of:

- `taker_ratio`
- `volume_spike`
- `volume_delta`

…while another rule on the same asset (typically `spread`) shows a real
value and `PASS`/`FAIL`. That mismatch proves the pipeline is running
overall but the microstructure data path is broken for that specific
symbol.

This runbook maps each output of the diagnostic endpoint to the
corresponding root cause and the fix to apply.

## Endpoint

```
GET /api/admin/symbol-health/{symbol}
Authorization: Bearer $ADMIN_DIAGNOSTICS_TOKEN
```

- Returns **404** if `ADMIN_DIAGNOSTICS_TOKEN` is unset (default-deny).
- Returns **401** without/with the wrong bearer token.
- Returns **200** with a JSON document on success.
- All probes are read-only — safe to invoke at any time.

The token is provisioned the same way as `PROMETHEUS_BEARER_TOKEN`
(Secret Manager + Cloud Build env injection). The endpoint is intended
to live behind the same `--ingress=internal-and-cloud-load-balancing`
perimeter as `/metrics` in production.

## Reading the output

Each top-level key is one independent subsystem. Probes never raise —
on internal failure they return `{"ok": false, "error": "..."}` so the
operator always sees the full picture.

### `pool_status`

```json
{
  "ok": true,
  "found": true,
  "any_approved_active_spot": true,
  "memberships": [{"pool_id": 7, "market_type": "spot",
                   "is_active": true, "is_approved": true, ...}]
}
```

| Symptom | Root cause | Fix |
|---|---|---|
| `found: false` | Symbol never added to any pool | Add via the Pools UI or POST `/api/pools/{id}/coins`. |
| `any_approved_active_spot: false` | Symbol is in a pool but not approved, not active, or only attached to futures pools | Approve via the Pool Approval UI; or attach to a `market_type='spot'` pool. |

### `resolver_diff`

```json
{
  "ok": true,
  "in_ws_subscription": false,
  "in_microstructure_scheduler": true,
  "drift_for_this_symbol": true,
  "drift_reason": "in microstructure scheduler but NOT in WS subscription..."
}
```

| Symptom | Root cause | Fix |
|---|---|---|
| `in_microstructure_scheduler: true`, `in_ws_subscription: false` | Symbol is approved but not attached to a `pools.market_type='spot'` row → microstructure scheduler picks it up but the WS leader does not subscribe → trade buffer never fills → `taker_ratio`/`volume_delta` rely on REST fallback only | Move the `pool_coin` to a spot pool, OR file a follow-up to align the two resolvers (use a shared helper in `pool_service`). |
| Both `false` | Symbol not approved | See `pool_status` table above. |
| Both `true` | Resolver drift is **not** the cause for this symbol — keep diagnosing. | n/a |

### `trade_buffer`

```json
{"ok": true, "key": "trades_buffer:spot:DOGE_USDT",
 "exists": true, "member_count": 17, "ttl_seconds": 300,
 "newest_trade_age_seconds": 1.2}
```

| Symptom | Root cause | Fix |
|---|---|---|
| `redis_available: false` | Redis client cannot init | Check Cloud Run logs for `[redis] async client init failed`; verify `REDIS_URL` secret. |
| `exists: false` and `in_ws_subscription: true` | WS leader is subscribed but never received trades for this symbol — most likely a low-volume symbol in a window with zero trades, OR the WS handler is failing silently | Cross-check `ws_leader_status` (is the leader actually elected?) and the Cloud Run logs for `[gate-ws-leader]` errors. |
| `newest_trade_age_seconds > 360` | Buffer TTL has expired (only old trades visible) | WS leader stopped writing; check leader heartbeat. |
| `ttl_seconds: null` | Key has no TTL set (writer bug) | File a bug — every write should carry `EXPIRE TRADE_BUFFER_TTL_SECONDS`. |

### `indicators_history`

The most recent 5 rows in the `indicators` table for this symbol, with
the list of keys present in `indicators_json`.

| Symptom | Root cause | Fix |
|---|---|---|
| Most recent row has `scheduler_group: "microstructure"` but `has_taker_ratio: false` | Microstructure scheduler ran, OHLCV present, but order_flow returned `None` → buffer empty + REST fallback also empty | Cross-check `trade_buffer` and `live_probes.order_flow_300s`. |
| Most recent row has `has_volume_spike: false` and `scheduler_group: "microstructure"` | OHLCV `df` was empty when scheduler ran → FeatureEngine produced nothing | Check `live_probes.ohlcv_5m` and Gate/Binance `[FETCH] RAW_LEN` logs. |
| `rows: []` | Symbol never had indicators computed | Confirm pool_status + resolver_diff first. |
| Only old rows (high `age_seconds`) | Scheduler stopped running for this symbol | Check microstructure scheduler logs for crash, or schema-drift error (`scheduler_group` missing). |

### `ohlcv_history`

Last row per timeframe in the `ohlcv` table. If both `5m` and `1h` are
`present: false`, OHLCV ingestion is broken end-to-end for this
symbol — check `[OHLCV]`/`[FETCH]` logs; very likely a Gate.io currency
pair mapping issue.

### `live_probes.orderbook_metrics`

Calls `market_data_service.fetch_orderbook_metrics(symbol)` live. This
is the same code path that produces the `spread_pct` value users
already see in the UI. Should always succeed for a real Gate.io
symbol — failure here means Gate.io REST is unreachable from Cloud Run.

### `live_probes.ohlcv_5m`

Calls `market_data_service.fetch_ohlcv(symbol, "5m", 100)` live.

| Symptom | Root cause |
|---|---|
| `rows: 0`, `exchange: null` | Both Gate.io and Binance returned empty / failed for this symbol → check Cloud Run logs for `[OHLCV]` errors. |
| `rows < 100`, `exchange: "gate.io"` | Gate.io has limited history for this symbol — should still be enough for `volume_spike(20)` if `rows >= 21`. |
| `exchange: "merged (...)"` | Gate.io was short and Binance fallback merged in — normal for newly-listed symbols. |

### `live_probes.order_flow_300s`

Calls `get_order_flow_data(symbol, 300)` live. This is the **definitive
test** of whether `taker_ratio`/`volume_delta` can be produced for this
symbol right now.

| Symptom | Root cause |
|---|---|
| `source: "gate_trades_ws_spot"` and values present | Buffer healthy — if the indicator is still missing in the watchlist, the bug is downstream (compute_indicators / scheduler not running for this symbol). |
| `source: "gate_io_trades"` and values present | WS buffer was empty, REST fallback succeeded — fine for high-volume symbols, fragile for low-volume. |
| `source: "gate_io_trades"`, all values `None` | Buffer empty AND REST returned no trades in the 300s window — symbol genuinely has no trades, or symbol mapping is wrong. |

### `ws_leader_status`

```json
{"ok": true, "leader_holder": "container-abc-1234", "elected": true,
 "leader_ttl_seconds": 25}
```

| Symptom | Root cause | Fix |
|---|---|---|
| `elected: false` | No leader has been elected → trade buffer is empty for **every** symbol | Check Cloud Run logs for `[gate-ws-leader]` errors, force a restart of the service. |
| `leader_ttl_seconds < 5` | Leader on the verge of expiring without renew → leader replica is stuck | Restart the service to force re-election. |

## Diagnostic workflow

1. Reproduce the user-reported symbol in the watchlist UI.
2. Run:
   ```bash
   curl -H "Authorization: Bearer $ADMIN_DIAGNOSTICS_TOKEN" \
     https://<cloud-run-host>/api/admin/symbol-health/DOGE_USDT \
     | jq
   ```
3. Walk the JSON top-to-bottom: `pool_status` → `resolver_diff` →
   `trade_buffer` → `live_probes.order_flow_300s`. Stop at the first
   subsystem that contradicts the expected healthy state.
4. Apply the fix from the table above. If the fix requires a code
   change, file a follow-up task scoped to the specific subsystem
   (`pool_service`, `gate_ws_leader`, `microstructure_scheduler`,
   `order_flow_service`).
5. Re-run the diagnostic to confirm the value is now populated and
   wait one full microstructure scheduler cycle (~5 min) before
   asking the user to re-check the watchlist.

## See also

- `backend/app/api/admin_diagnostics.py` — endpoint implementation
- `backend/app/services/microstructure_scheduler_service.py` — what
  feeds these indicators
- `backend/app/services/gate_ws_leader.py` — WS subscription resolver
- `backend/app/services/order_flow_service.py` — buffer-first order
  flow computation
- `backend/app/websocket/event_handlers.py` — trade buffer writer
