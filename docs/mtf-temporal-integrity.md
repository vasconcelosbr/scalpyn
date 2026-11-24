# MTF: temporal integrity and decision evidence

The BTC observation `f0650aa6-be97-4cf7-a0cb-eb70946d5362` was retrieved read-only from production. Its frozen engine is `mtf_chart_v1`, not the subsequently published complete JSON engine. It records `SHADOW`, `operational_effect: false`, an `ENGULF_BULL` trigger, structural trend `UP`, BTC context `DOWN` with `exempt: true`, and no vetoes. This does not demonstrate a real exchange purchase or a breakout rule using the auxiliary microstructure close.

The historical observation contains final candles, pivots, patterns, ATR and diagnostics, but not all input candles or rule series. Attempting to substitute current database OHLCV failed to match the captured last candles and lacked the required structural data. Full historical reproduction is therefore explicitly **NOT_REPRODUCIBLE / LEGACY_INPUTS_NOT_FROZEN**. Do not rewrite that snapshot or present a reconstruction as the original execution.

## Correction

FeatureEngine now freezes the closed-candle domain before computing technical values when a timeframe is supplied (or microstructure implies its timeframe). Closing timestamps and explicit open flags are respected; epoch seconds are normalized. The same domain feeds close, averages, slopes and price-position calculations. Legacy callers without temporal columns retain their existing numerical behavior and receive no fabricated clock.

Calculation provenance travels alongside scalar values and is persisted per indicator by `envelop_results`. It includes candle open/close, base close, sample count and configuration hash. Price distances additionally carry their exact reference value and percent formula. Live market overrides do not inherit the newly generated candle provenance.

No strategy thresholds, score weights, veto switches, pattern lists, profile configurations or BTC exemption policy are changed. Existing MTF missing-data guards and native/additional veto composition remain active.

New history snapshots freeze the decision inputs (candles, pivots, patterns, ATR, configuration, spread, BTC trend, flow, rule series and evaluation cutoff) with a content hash. Authenticated history details replay the pure decision function and compare state, plan, score, vetoes and trigger. This is decision replay using frozen intermediate indicator values, not an independent recomputation of every technical indicator. Hash mismatch fails verification; old snapshots remain explicitly unreproducible. No exchange or current-data calls occur during replay.

The history UI explains mandatory checks versus score contributions, BTC exemption, MTF base close and ATR, and the replay result. Auxiliary producers retain their separate identity. Structural-display-only values are no longer labeled available for evaluation.

## Verification

Regression tests mutate an unfinished candle and verify stable technical results, validate distance formulas against their recorded references, check epoch timestamp handling and explicit open flags, and replay a triggered decision without allowing auxiliary context values to affect it. Existing MTF, producer merge, source-clock and scheduler tests remain applicable. PostgreSQL integration runs in CI.

Operational evidence and the unmodified source snapshot are retained locally in `Downloads/BTC-temporal-fix`; historical data and user configuration are not rewritten during release.
