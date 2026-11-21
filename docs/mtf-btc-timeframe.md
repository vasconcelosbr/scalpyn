# Configurable Bitcoin structural veto

The MTF configuration accepts `btc_trend_timeframe`: `4h` (legacy default) or
`1h`. The selected structural state is passed to the existing evaluator.
`trend()`, pivots, asset vetoes, scoring and plan construction are unchanged.

## Identity and historical compatibility

`MTFChartConfig.engine_dump()` omits the field when it is `4h`, including when
explicitly saved. Legacy configuration hashes therefore remain unchanged.
`1h` participates in the hash used by evaluations and Shadow reporting.
Changing back to `4h`, with all other settings unchanged, restores the original
configuration identity and selection without a deployment.

Historical payloads need no migration or backfill. The current L3 projection
still requires the active configuration hash: after a timeframe change, an old
reading is temporarily `CONFIG_CHANGED` until a fresh matching observation
arrives. Historical detail/export remains readable; old decisions are never
relabeled as decisions from the new configuration.

Save through `PUT /api/mtf/config` / the existing configuration service.
The settings dialog exposes the selector in Vetos. Preserve the other fields.

## Frozen evidence

Every completed chart evaluation adds `analysis.btc_context`, version
`mtf_btc_context_v1`, with both timeframe states, the exact last high/low pivot
pairs consumed by the classifier (including confirmation times), reference
candle close price and timestamps, window start/count, pivot settings, candle
limits, active timeframe and enabled/exempt flags.

`analysis.btc_trend` remains the selected state for compatibility.
`analysis.btc_trend_timeframe` identifies its timeframe. The history snapshot
already preserves the analysis object without selecting fields.

BTC watchlist evaluations reuse their prepared frames. Other assets use the
existing process-local market cache and configured timeframe limits, Gate Spot
with `gate_only=True`, and the existing closed-candle preparation.

## Missing context

For other symbols, no closed candles, stale BTC data or invalid/gapped candles
produce an unavailable context; the existing enabled veto adds
`BTC_CONTEXT_UNAVAILABLE`. Valid candles with insufficient pivot pairs still
classify as `RANGE`, exactly as `trend()` did before. BTC itself remains exempt;
its legacy self-context classification is preserved.

Unexpected exceptions in the active timeframe retain the existing error path.
An exception in the additional observational timeframe is recorded as
unavailable and cannot break the active decision. Disabling the BTC veto
continues to bypass that veto, while both contexts are still collected.

## Verification

`test_mtf_btc_timeframe.py` covers actual chart preparation, frozen history
snapshots, legacy decision parity, hourly gate changes, insufficient/stale/gapped
data, Gate-only cache reuse, exclusion of forming candles, BTC exemption and L3
legacy validation/configuration reversal. API save and frontend rendering tests
cover the normal configuration path and both displayed states.

The retrospective report is a separate read-only artifact. Individual replay
outcomes are descriptive and do not reproduce portfolio admission or establish
an expected return. No retrospective results are written into trading history.
