# Spot invariant resolution report

## Decision state

`HUMAN_DECISION_REQUIRED_BEFORE_PRODUCTION_MUTATION`.

The repository invariant says Spot must never sell at a loss. The prior forensic evidence recorded active `spot_engine.never_sell_at_loss=false`. This implementation did not change that production configuration and did not infer precedence `[NÃO REALIZADO]`.

The systemic runtime now blocks Spot candidate/proposal paths when the resolved invariant state is false and independently denies live trading, model promotion and real-risk mutation authority `[test: test_spot_invariant_blocks_mutation_authority]`.

Before any production activation that can propose Spot exits, a human must:

1. inspect the complete active Spot JSON and all consumers;
2. decide the intended semantic and precedence;
3. create a new immutable configuration version;
4. reconcile affected routes in shadow mode;
5. approve the exact version change separately.

No Spot config, Auto-Pilot flag, model champion, order or real position was changed `[test/config reconciliation]`.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| invariant guard tests=`1` | `[test] mandatory suite` | `test_spot_invariant_blocks_mutation_authority PASSED` |
