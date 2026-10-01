# Pump exact price paths / isolated ML preparation

Follow-up to the approved Pump plan and PR246. The UI/score remains purely
observational. No Shadow correction, trainer activation or Pool connection.

`232_pump_exact_price_paths` adds an owner/instrument/minute/content-hash archive.
The existing authorized raw market read is reused; no additional feed requests.
Only the most recently closed minute is archived on each Pump pass. Source
coverage, liveness, gaps, invalid values and truncation accompany every path.
Raw price timestamps/IDs are preserved. Distinct revisions are appended rather
than overwriting a partial read. Labels prefer a complete captured revision.

Provisional bounded capture: `price_paths_enabled=false` initially, at most
3,000 price points per asset/minute and 10,000 per cycle, inside the existing
4-second transaction budget and shared Pump-only 1,000,000,000-byte storage cap.
An exceeded point cap is explicit unknown coverage, not a sampled complete
path. The raw price buffer is not modified. Capture or labels yield first on
budget/time failures, keeping the old Pump cycle and all execution untouched.

To create NEW exact observations, publish label specification version
`pump_gross_touch_v3`, resolution `trades_exact_window_v1`, future price policy
`last_trade_asof_endpoint_v1`, maximum endpoint quote age 60 seconds and settle
63 seconds. Initial reference remains the contemporary Gate ask. Future paths
are observed trades, not execution prices. Include only timestamps between
decision and endpoint; boundary highs from outside the window cannot count.
Endpoint uses the last observed trade at/before the exact endpoint with its
age recorded. It cannot use a later print or interpolate a gap. Completeness
requires every overlapping minute's certified source coverage. First hit time
is exact to the captured trade timestamp only when prior coverage is complete;
otherwise it stays censored. Same timestamp TP/downside remains ambiguous.
Labels v2/v1 stay intact with their original contracts.

The current partial endpoint minute is archived after closing, so label
availability may lag the five-minute horizon by a minute. This never prevents
the current radar from displaying opportunities or imposes an entry/exit wait.

`scripts.train_pump_challenger` consumes a bounded streamed JSONL export and
an explicit frozen budget/support/split manifest. Heavy libraries load only
after independent support and contract gates pass. It trains a distinct
XGBoost + calibrator in `pump_ml/`; no production model pointer or score delta.
The `pump_ml` Celery lane and disabled worker manifest are reserved separately.
The registered task currently reports the unapproved budget/support block;
there is no consumer or beat schedule. Actual training is the explicit offline
CLI, not a claim that a production model was trained.

Remaining statistical prerequisites include verified listing identities,
independent episodes/days/regimes, explicit feature/cost/split contracts,
cluster confidence intervals, ablations and measured resource headroom.
Never treat a newly captured path as a validated model or promote automatically.
