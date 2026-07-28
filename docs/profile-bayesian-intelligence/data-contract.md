# Profile Bayesian Intelligence — Data Contract

## Source and immutability

The initial dataset reads `shadow_trades` with `SELECT` only. Source rows are
never updated, backfilled, relabeled, or copied into ML training tables.

Only entry-time `features_snapshot` values enter the indicator matrix.
`features_snapshot_exit` and post-outcome indicator values are excluded to
prevent leakage. Outcome and net PnL are labels, not features.

## Inclusion contract

- The row belongs to the authenticated user and selected profile.
- The row is closed inside the requested temporal window.
- The row has a stable observation ID (`event_id`, falling back to row ID).
- The entry snapshot is an object.
- Outcome belongs to the recognized TP, SL, or timeout vocabulary.
- The operational TP/SL policy matches the selected policy hash.
- Source, lineage status, barrier mode, and barrier contract version match the
  explicit persisted policy.
- Entry time is at or after the policy's versioned population frontier.
- Selection is a declared contiguous temporal window; random truncation is not
  supported.

If the window contains more than one incompatible TP/SL policy and the caller
does not select one, the build fails. Policies are never mixed implicitly.

## Indicator normalization

The canonical catalog supports RSI, ADX, Delta ADX, DI+/DI-, ATR%, Bollinger
width, z-score, MACD, MACD histogram, EMA alignment, VWAP distance, volume
delta, taker ratio, order-book pressure, spread, volume, component scores, and
total score when present.

- Missing values remain missing.
- Invalid, infinite, and non-numeric values become missing, never zero.
- Constant indicators are removed from model fitting.
- Indicators below the configured coverage policy are removed.
- Indicators must also pass coverage by entry day × entry ATR bucket and the
  missingness/outcome association gate in both discovery and validation.
- The dedicated `atr_pct_at_entry` column overrides a snapshot ATR
  representation; entry-time BTC change and market-data confidence are exposed
  as controls when coverage permits.
- For retained indicators, missingness receives an explicit indicator column.
- Standardization parameters are stored in the run context.

## Reproducibility

Every dataset snapshot stores:

- ordered observation IDs;
- dataset and policy hashes;
- temporal interval;
- inclusion and exclusion criteria;
- direct counts by profile, symbol, regime, outcome, day, and operational policy;
- duplicate and invalid row IDs;
- source-table declaration and contract version.

The canonical hash is computed over deterministically ordered normalized
observations.

## Sample-size semantics

- `direct_sample_size`: selected profile observations.
- `shared_sample_size`: observations borrowed by an explicitly broader
  hierarchical dataset. The initial profile-scoped builder reports zero.
- `min_mcmc_effective_sample_size`: posterior sampling diagnostic; it is not a trade
  count.
