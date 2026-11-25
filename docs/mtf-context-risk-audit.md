# MTF context and risk audit

`context_audit` is an optional, disabled-by-default configuration block. When enabled,
it enforces independent gates after existing rules and plan calculation. No score,
custom OR group or BTC exemption overrides a failed audit. No real orders are sent.
All thresholds are schema-backed and GUI/advanced-JSON editable.

## Semantics

Only confirmed pivots and closed candles available at the decision cutoff are used.
Each upper stage must be UP for Spot LONG. A closed break below the last confirmed HL
invalidates that structure until a new complete directional structure forms after the
break. Equal closes only test the level. Unknown structure is unavailable, not neutral.
The operational close must exceed the latest confirmed high when labelled LH.

The chosen stop is retained, never widened automatically. It must be below the latest
confirmed operational low and the arming support's lower boundary. The audit compares
its distance to `min_stop_atr_trigger` and reference-target RR to `min_reference_rr_gross`.
The reference target is `stop_sizing.target_pct` even when structural stop sizing is used.
Structural TP1/TP2 and their RR remain separate. Net reference reward uses existing fees
and slippage. Defaults are experimental, not statistically established optimal settings.

Context/risk failures produce NO_TRADE; a missing operational LH break leaves a valid
candidate ARMED. Audit errors fail closed. Intrabar projections never publish eligibility.
Frozen historical decisions/positions are not edited. History capture remains enabled
while the audit is enabled, even if the optional general-history toggle is disabled,
so rejected candidates and subsequent standard outcome horizons remain available.

## External consumption contract

Read current authenticated `/api/mtf/chart`, `/api/mtf/watchlist`, or the Consolidated
observation feed. The additive `signal_contract` contains `signal_id`, `signal_status`,
`eligible`, `confirmed_at`, `expires_at`, `config_hash`, `audit_version`, and read-time
`evaluated_at`. Consolidated items expose it under `mtf.signal_contract` (and under
the full analysis when requested). Eligibility is a signal-quality classification,
not account/order authorization. The external robot remains outside this delivery.

A compliant consumer requires all of: eligible=true, TRIGGERED, risk_audit PASS,
known audit version, current config hash, a closed candle and unexpired deadline.
Reject missing fields, unknown versions, stale responses and previews; deduplicate
by signal_id. Re-fetch before acting. Historical exports are evidence, not live signals.
The websocket remains preview-only and cannot be used as an execution source.
Existing consumers that only inspect candlestick patterns are not protected by this contract.

## Validation and rollout

Run `python -m scripts.audit_mtf_context_replays INPUT.jsonl --output REPORT.json`
on owner-scoped replay exports. The comparator first requires the original replay to
match, retains its frozen stop, then reports each audit failure and the combined result.
It splits chronological timestamps without fitting thresholds. Missing evidence and
replay mismatches are exclusions, never fabricated outcomes.

Only activate after tests, source guard, terminal provider success and authenticated
UI verification. Save via config API, verify fresh-process reads and compare all other
configuration fields. Config hashes invalidate prior pending signals; no open position
is resized. A failed audit stays blocked; there is no permissive runtime fallback.
Financial effectiveness and external robot compliance require separate evidence.
