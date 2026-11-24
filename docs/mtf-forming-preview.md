# MTF forming-candle preview

Chart responses with display data include `forming_preview`, separate from the official `analysis`. The preview recalculates technical values and pattern conditions using the open OHLCV candle at each existing configured chart poll. Its refresh interval and observation time are visible; this is polling, not an exchange tick stream.

The official evaluator, observations, entry band, status badges and audio still consume the closed-candle analysis. Scheduled observers (`include_display=False`) do not calculate or receive preview data. The preview exposes only checks and pattern diagnostics, never hypothetical status, plan or trigger fields. No configuration or trading parameters are changed.

Preview frames use a working copy of candles. Confirmed pivots are retained from official frames. BTC trend remains closed-candle context. Declarative indicator rules can read their forming samples only when explicitly invoked with `preview_at`; natural candle timestamps and prior closed samples remain intact, including crossover comparisons. Normal evaluation defaults retain their causal cutoff.

Missing, future, duplicate, invalid or discontinuous forming data never falls back to a closed sample labeled live. The preview expires at its configured freshness boundary or the forming candle's close, whichever comes first. The UI validates symbol, configuration, contract and timestamps, and waits for a fresh response on expiry.

Each readiness condition is a single compact line: label/timeframe, current value, target and remaining difference. Narrow screens scroll the line horizontally. Changes animate and respect reduced motion. Satisfied provisional checks do not confirm a trade: the UI explicitly waits for the close.

Regression coverage includes moving open OHLCV with unchanged official analysis, immutable inputs and settings, higher-timeframe live indicators, natural-timestamp crossover previews, missing/future data, observer exclusion, and client expiry/context isolation.
