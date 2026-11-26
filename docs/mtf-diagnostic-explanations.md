# Frozen MTF diagnostic explanations

The candidate entry band now resolves every risk veto against the checks belonging to the same analysis. Failed checks, unavailable inputs and missing historical details have distinct labels. Public titles no longer expose internal stage names.

Each diagnostic card includes an always-visible description and a keyboard/touch-accessible detail section. The section shows the method, frozen parameters, accepted comparison, timeframes, available history or indicator sample, structural pivots and active/historical breaks. JSON operands include both source configurations and sample timestamps; crossing checks retain previous operands. Group checks expose the E/OU tree and its evaluated states, including disabled and unavailable branches.

## Contract

- Additive `explanation` and `rule_tree` fields on checks; `explanation_context` on trigger diagnostics.
- `mtf_check_explanation_v1` describes presentation evidence. It is not a strategy configuration version.
- Metadata is produced using the same configuration and inputs as the evaluation. The frontend does not load current settings to fill old records.
- History windows are explicitly distinguished from the sample actually passed to an indicator.
- BTC evidence is taken from its frozen context, never from the asset's candles.
- The risk audit's pivot directions and integrity remain separate checks. Incomplete BTC pivots retain the existing classifier behavior and are disclosed in the detail.
- No configuration schema, migration, entry policy, scoring threshold, order path or signal identity changed.
- The input hash of a newly frozen replay can reflect the added descriptive sample metadata; existing stored inputs are not rewritten and replay verifies their original hash.

## Validation evidence

Commands were run from their owning backend/frontend directories.

```text
python -m pytest tests/test_mtf_diagnostic_explanations.py tests/test_mtf_chart_engine.py tests/test_mtf_risk_audit.py tests/test_mtf_complete_json.py tests/test_mtf_trigger_diagnostics.py tests/test_mtf_forming_preview.py tests/test_mtf_btc_timeframe.py tests/test_mtf_btc_market.py tests/test_mtf_signal_replay.py tests/test_mtf_entry_band.py -q
122 passed, 4 skipped in 8.89s

node --import tsx --test lib/mtf*.test.ts
tests 42
pass 42
fail 0

npx tsc --noEmit
exit 0

npm run build
Compiled successfully
exit 0
```

A direct comparison loaded the original engine and rule evaluator from the baseline commit and evaluated synthetic scenarios against the changed implementation. The comparison includes decision status, plan, score, vetoes, trigger, candidate status and signal publication identity. Its source and per-case results are in [decision-parity.json](evidence/mtf-diagnostic-explanations/decision-parity.json).

```json
{"source":"66f77aea804868e80e1a7e28af59efd7f8dc42cc","cases":36,"mismatches":0}
```

The fixture is synthetic and does not measure market performance.

| Value | Provenance | Literal output |
|---|---|---|
| Backend tests | [query: pytest] | `122 passed, 4 skipped` |
| Frontend tests | [query: node test] | `tests 42; pass 42; fail 0` |
| Before/after comparison | [query: decision-parity.json] | `"cases": 36, "mismatches": 0` |

## Remaining release validation

The browser runtime failed before executing any page operation with `failed to write kernel assets: O sistema não pode encontrar o caminho especificado. (os error 3)`, including after resetting the runtime. Authenticated visual validation is therefore not confirmed.

A standalone preview was rendered from the application components using clearly labelled synthetic fixtures for local review. It is not evidence of production deployment or authenticated acceptance. Production release and its provider/runtime proof remain separate from this implementation.
