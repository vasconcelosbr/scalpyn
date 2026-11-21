# Structural indicators in new MTF observations

The history worker calls `chart_data(..., include_display=False)`. Indicator
families are selected by the profile, so a profile without EMA rules formerly
produced an ATR/volume snapshot without EMA50. The structural projection needs
that EMA to display `(closed_close - ema50) / atr`.

`mtf_structure_indicators.py` declares the display contract centrally:
`ema50` and `atr`. After `chart_data` has finalized the complete analysis,
`mtf_history.capture` enriches only its returned frames. It reuses
`FeatureEngine._calc_ema` and the frame's existing producer sample window.
It computes only the latest closed EMA50 sample, independently of the profile's
indicator families. Existing EMA series and the engine-owned ATR are preserved.
No extra candle fetch or drawing history is required.

An added sample has role `structural_display_only`, its explicit EMA configuration,
and separate identity `mtf_structure_indicators_v1` plus `sample_count`.
This identifies presentation provenance without changing the analysis's
`indicator_identity`, `profile_hash`, `config_hash`, `engine_version`, or
observation deduplication key. Existing profile EMA samples keep their original
provenance. Shadow grouping and L3 validation continue using the original
analysis identities.

The formula needs at least 50 closed candles and the complete existing producer
window. Insufficient history leaves EMA unavailable. Missing ATR is never
fabricated; the projection continues returning null and the UI an em dash.
There is no extension classification or trading policy added here.

Historical observations are not rewritten. Old snapshots without EMA remain
readable and eligible for L3 presentation subject to the unchanged age, profile,
configuration, quality and engine validation. Their extension remains null.

The structural extension is distinct from the EMA21-based `extension_atr` in
`mtf_observation_service`, and from general indicator/ML EMA inputs. This change
does not feed those consumers or alter missing-input behavior in the engine.

Validation: `tests/test_mtf_structure_indicators.py` covers scheduled capture,
all four decision states, exact full-analysis and evaluation equality, producer
EMA parity, insufficient warmup, open-candle exclusion, missing ATR, idempotence,
and legacy L3 validation/projection. No migration or backfill is involved.
