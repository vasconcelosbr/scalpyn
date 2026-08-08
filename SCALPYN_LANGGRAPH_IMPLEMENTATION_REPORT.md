# Scalpyn LangGraph implementation report

Verdict before staging: `PARTIALLY_IMPLEMENTED_BLOCKED_BY_INFRA`.

## Outcome

LangGraph is installed above the existing systemic AI foundation without replacing canonical DB records, Celery or provider adapters. The implementation adds exact dependency pins plus a hash lock, a dedicated PostgreSQL checkpoint schema, immutable graph registry, four graphs, five canonical graph tables, five durable Celery tasks on an isolated queue, tenant-safe APIs, human interrupts, recovery/cancel paths, event reconciliation, telemetry, retention administration and the authenticated “Intelligence Runs” frontend.

Runtime defaults remain `native` with every LangGraph feature flag false. `LIVE_WRITE` is denied. No production configuration, model champion, Auto-Pilot flag, order or real position has been changed.

## Runtime architecture

```mermaid
flowchart LR
    UI["Intelligence Runs"] --> API["Tenant-scoped graph API"]
    API --> CANON["Canonical AI request and graph run"]
    CANON --> Q["ai_orchestration queue"]
    Q --> WORKER["Dedicated worker"]
    WORKER --> GRAPH["Versioned LangGraph"]
    GRAPH --> CHECK["AsyncPostgresSaver / langgraph_runtime"]
    GRAPH --> AUDIT["Canonical events, interrupts, results and usage"]
```

## Systemic analysis graph

```mermaid
flowchart TD
    START --> load_request --> authorize_tenant --> resolve_provider_model --> resolve_prompt
    resolve_prompt --> freeze_canonical_dataset --> resolve_configuration_bundle --> run_data_quality_gate
    run_data_quality_gate --> retrieve_decision_memory --> plan_typed_tools --> execute_readonly_tools
    execute_readonly_tools --> assemble_evidence --> invoke_provider --> validate_structured_output
    validate_structured_output --> persist_result_usage_audit --> complete --> END
```

## Root-cause graph

```mermaid
flowchart TD
    START --> identify_change_window --> load_before_after_versions --> validate_comparability
    validate_comparability --> compare_market_regime --> compare_data_quality --> compare_symbol_profile_concentration
    compare_symbol_profile_concentration --> compare_exit_policy --> compare_model_feature_contract
    compare_model_feature_contract --> run_paired_replay_when_available --> classify_root_cause
    classify_root_cause --> generate_evidence_bound_diagnosis --> persist_result_usage_audit --> END
```

## Regenerative shadow graph

```mermaid
flowchart TD
    START --> validate_dataset_and_bundle --> classify_root_cause --> create_hypothesis
    create_hypothesis --> retrieve_similar_decision_memory --> check_do_not_repeat_context
    check_do_not_repeat_context --> design_ablation_candidates --> H1{"Human candidate approval"}
    H1 -->|reject| END
    H1 -->|approve/edit| create_immutable_candidate_versions --> start_shadow_experiment
    start_shadow_experiment --> H2{"Wait for shadow evidence"}
    H2 --> evaluate_champion_challenger --> propose_keep_reject_or_rollback --> H3{"Final human decision"}
    H3 -->|reject| END
    H3 -->|approve/edit| shadow_only_change_set --> persist_experiment_outcome --> persist_decision_memory --> END
```

## Verification state

- Mandatory LangGraph suite: `37 passed` `[test]`.
- Combined critical suite: `85 passed` `[test]`.
- Full backend collection: `1573 collected, 0 collection errors` `[test]`.
- Frontend tests: `23 passed` `[test]`.
- TypeScript: passed `[test]`.
- Production build: passed; `/intelligence-runs` present `[test]`.
- Scoped lint for changed frontend files: `0 errors` `[test]`.
- Global lint: `371 errors, 62 warnings` in legacy baseline `[test]`.
- Full backend execution stopped at `20 failures` after `278 passed`; failures are recorded verbatim in `LANGGRAPH_BACKEND_FULL_SUITE.txt` `[test]`.
- Hash-locked dependency resolution and `pip check`: passed `[test]`.

## Deployment status

Isolated staging resources are being prepared. Production is deliberately stopped at the explicit human checkpoint defined by the prompt. Paid provider canary is not authorized; it will not run on inferred consent.

## Rollback

Keep all flags false to make the new paths inert. If staging fails, stop the dedicated worker and restore the prior service deployment. Database rollback requires a backup because downgrade deletes checkpoint history. Application canonical records should be retained for audit even if checkpoint rows are removed under an approved retention policy.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| critical tests=`85` | `[test] pytest` | `85 passed` |
| mandatory tests=`37` | `[test] named suite` | `37 passed` |
| backend collected=`1573` | `[test] collect-only` | `1573 tests collected` |
| frontend tests=`23` | `[test] npm test` | `pass 23; fail 0` |
| frontend routes=`44` | `[test] next build` | `Generating static pages (44/44)` |
| graph definitions=`4` | `[test] registry/migration comparison` | `registry_migration_match=4` |
| production orders=`0` | `[config reconciliation]` | no production actions executed before checkpoint |
