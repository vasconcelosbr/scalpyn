# Pump market-cap universe and label drain

Pump-only effective configuration seeds the operator-approved PUMPPOOL condition
`market_cap >= 1_000_000_000 USD` (profile
`0292dc92-9deb-4ec9-af23-ccc418a3d5eb`, condition `cond_1790779717206`).
`universe_filter.min_market_cap_usd` is editable through the existing authenticated
`PUT /api/pump-monitor/config`; no pool/profile membership is edited. A value of
zero disables the cap subset. Unknown, non-positive and non-finite caps are
excluded when the minimum is enabled, including an entirely empty source map.

The source is the same `market_metadata.market_cap` used by Pool discovery
(CoinMarketCap USD quote, Gate fallback). `last_updated` also tracks ticker/book
changes and does **not** certify cap age. Diagnostic envelopes/logs explicitly
state `market_cap_freshness=not_certified`. CMC symbol collisions and source age
remain limitations; no artificial freshness timestamp is added.

Only eligible symbols build rows, scores, alerts, snapshots or new observations.
The existing REALTIME sync receives only these scored rows and retains its normal
hysteresis. The configured source pool, other profiles and existing positions
remain unchanged.

Excluded/removed symbols with existing unlabelled observations continue price
collection through the maximum configured endpoint, inclusive. These rows carry
`categorical._research_role=label_drain`, empty indicator arrays and no score,
alerts or pool membership. They serve as OHLC endpoint evidence only, never label
targets or exported analytical observations. They cannot extend their own drain
deadline. Collection stops automatically after the endpoint, even if the hourly
label job has not run. The label job can then finish from persisted evidence.
No historical rows or existing labels are deleted or recomputed. Missing trades
still yield nulls; drain is not a promise of tradable prices.

Consumers querying the research table directly must exclude label_drain rows
from observation populations; the maintained export script does so. Record
config_hash and market_cap_usd observed on new rows rather than applying today's
cap retrospectively. Existing NULL categorical rows remain normal observations.

Release verification: exact main commit in API, Pump and research workers;
PUMP-UNIVERSE logs with candidate/eligible/excluded/drain counts; fresh snapshots
with recorded cap >= minimum; price-only drain score/alerts/membership absent;
label counts and endpoint continuity; no Pool/profile mutation. No migration.

Rollback must be a reviewed main revert preserving the remaining production
invariants, followed by normal deployments. Prefer authenticated Pump config
minimum=0 for threshold rollback while retaining correct price-only row handling;
never deploy an old branch snapshot or delete/relabel historical data.
