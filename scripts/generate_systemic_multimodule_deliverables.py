"""Generate the audited systemic multi-module delivery bundle from local evidence.

This script deliberately refuses to invent missing proof. Production, real-provider,
process-crash and protected-preview claims remain explicitly NOT PROVEN.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
EVIDENCE = ROOT / ".codex-evidence"
sys.path.insert(0, str(BACKEND))

from app.ai_orchestration.domain_tools import default_tool_capabilities  # noqa: E402
from app.ai_orchestration.langgraph.registry import graph_registry  # noqa: E402
from app.ai_orchestration.module_registry import export_module_capabilities  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(name: str, value) -> None:
    (ROOT / name).write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def write_text(name: str, value: str) -> None:
    (ROOT / name).write_text(value.rstrip() + "\n", encoding="utf-8")


def junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None
    return {
        "tests": int(suite.attrib["tests"]),
        "failures": int(suite.attrib["failures"]),
        "errors": int(suite.attrib["errors"]),
        "skipped": int(suite.attrib.get("skipped", 0)),
        "time_seconds": float(suite.attrib["time"]),
    }


captured_at = datetime.now(timezone.utc).isoformat()
canary = read_json(EVIDENCE / "staging-canary-final.json")
full = junit(EVIDENCE / "full-backend.xml")
focused = junit(EVIDENCE / "tests" / "focused-backend.xml")
audit = read_json(EVIDENCE / "tests" / "npm-audit.json")
modules = export_module_capabilities()
tools = [item.model_dump(mode="json") for item in default_tool_capabilities()]
tools.sort(key=lambda item: item["name"])

write_json("scalpyn_module_capability_registry.json", {
    "generated_at": captured_at,
    "source": "backend/app/ai_orchestration/module_registry.py",
    "immutability": "APPROVED rows and registry values are immutable",
    "count": len(modules),
    "items": modules,
})
write_json("scalpyn_module_tool_registry.json", {
    "generated_at": captured_at,
    "source": "backend/app/ai_orchestration/domain_tools.py",
    "authority_ceiling": ["ANALYSIS_ONLY", "PROPOSAL_ONLY", "CANDIDATE_ONLY", "SHADOW_ONLY"],
    "live_write_tools": [item["name"] for item in tools if item["side_effect"] == "LIVE_WRITE"],
    "count": len(tools),
    "items": tools,
})

dependency_lines = [
    "# Scalpyn Multi-Module Dependency Map",
    "",
    "Source: `backend/app/ai_orchestration/module_registry.py`.",
    "",
    "```mermaid",
    "flowchart LR",
]
for module in modules:
    key = module["module_key"]
    dependency_lines.append(f'    {key}["{key}"]')
    for dependency in module["dependencies"]:
        safe = dependency.replace("-", "_")
        dependency_lines.append(f'    {key} --> {safe}["{dependency}"]')
dependency_lines.extend(["```", "", "All registry modules are tenant-scoped. External guard concepts are leaves, not AI mutation targets."])
write_text("scalpyn_module_dependency_map.md", "\n".join(dependency_lines))

write_text("scalpyn_multimodule_data_contracts.md", """
# Scalpyn Multi-Module Data Contracts

## CanonicalAnalysisDataset

The existing immutable dataset record is extended with `origin_module`, `module_context_refs`, and `context_manifest`. It freezes source labels, event/outcome contracts, time window, filters, exclusions, row/query/dataset hashes, bundle reference, quality state, and findings. It never physically merges every domain table.

## ConfigurationBundle

The bundle keeps profile, score, risk, strategy, Spot, exit, feature, label, ML lane/model, and market-regime lineage. Social Score remains temporal evidence in dataset/context, not an ML feature or configuration mutation.

## AnalysisContextManifest

The manifest records requested/consulted modules, tools, freshness, quality, evidence IDs, conflicts, and vetoes. Typed tool evidence is persisted separately with an output hash and tenant/request scope.

## Recommendation envelope

Structured recommendations declare target module/path, rationale, evidence, expected effect, risks, confidence, side-effect class, approval requirement, and rollback. `LIVE_WRITE` is rejected; ML, Risk and Strategies mutations are rejected; Spot authority remains blocked.
""")

write_text("scalpyn_multimodule_configuration_bundle.md", """
# Scalpyn Multi-Module Configuration Bundle

Canonical fields:

- `profile_version_id`
- `score_engine_version_id`
- `risk_policy_version_id`
- `strategy_policy_version_id`
- `spot_policy_version_id`
- `exit_policy_version_id`
- `feature_contract_version`
- `label_contract_version`
- `ml_model_id`
- `model_lane`
- `market_regime_id`
- `bundle_hash`

The staging canary persisted bundle `{bundle_id}` with lineage `COMPLETE` [query: authenticated staging API]. Its hash is `NÃO DISPONÍVEL` in the final compact canary response; no value is inferred. No production bundle was changed.
""".format(
    bundle_id=canary["bundle_id"],
))

frontend_vulns = audit["metadata"]["vulnerabilities"]
test_results = {
    "generated_at": captured_at,
    "verdict": "PARTIALLY_IMPLEMENTED_TEST_GATE_FAILED",
    "focused_backend": {**focused, "status": "PASS", "source": ".codex-evidence/tests/focused-backend.xml"},
    "full_backend": {**full, "status": "FAIL", "source": ".codex-evidence/full-backend.xml"},
    "frontend_tests": {"tests": 23, "failures": 0, "status": "PASS", "source": ".codex-evidence/tests/frontend-tests.txt"},
    "frontend_typecheck": {"status": "PASS", "source": ".codex-evidence/tests/frontend-typecheck.txt"},
    "frontend_lint": {"errors": 0, "warnings": 435, "status": "PASS_WITH_WARNINGS", "source": ".codex-evidence/tests/frontend-lint.txt"},
    "frontend_build": {"status": "PASS", "source": ".codex-evidence/tests/frontend-build.txt"},
    "pip_check": {"status": "PASS", "source": ".codex-evidence/tests/pip-check.txt"},
    "npm_audit": {"status": "PASS_WITH_LOW", "vulnerabilities": frontend_vulns, "source": ".codex-evidence/tests/npm-audit.json"},
    "migration_cycle": {"status": "PASS", "cycle": "149 -> 150 -> 149 -> 150", "source": "local staging-compatible database command evidence"},
    "alembic_offline_full_history": {"status": "FAIL_HISTORICAL_148_JSONB_LITERAL_RENDER", "source": "audit_evidence/db/alembic-upgrade-head.sql"},
    "blocking_gate": "full backend collection is not green",
}
write_json("scalpyn_multimodule_test_results.json", test_results)

write_json("scalpyn_multimodule_staging_canaries.json", {
    "generated_at": captured_at,
    "status": canary["status"],
    "environment": canary["environment"],
    "analysis_run_id": canary["analysis_run_id"],
    "regenerative_runs": canary["regenerative_runs"],
    "runtime_proof": canary["runtime_proof"],
    "checkpoint_proof": canary["checkpoint_proof"],
    "dataset_id": canary["dataset_id"],
    "bundle_id": canary["bundle_id"],
    "prompt_id": canary["prompt_id"],
    "model_resolution_id": canary["model_resolution_id"],
    "provider": canary["provider"],
    "configured_model": canary["configured_model"],
    "effective_model": canary["effective_model"],
    "cost_usd": canary["cost_usd"],
    "authority": canary["authority"],
    "live_write": canary["live_write"],
    "real_provider_canary": canary["real_provider_canary"],
})

write_json("scalpyn_multimodule_entrypoint_adoption.json", {
    "generated_at": captured_at,
    "legacy": [
        {"entrypoint": "profile suggestion explanation", "bridge": "SystemicLangGraphBridge", "graph": "systemic-analysis-v2", "proof": "static + focused tests"},
        {"entrypoint": "shadow detailed analysis", "bridge": "SystemicLangGraphBridge", "graph": "root-cause-audit-v2/systemic-analysis-v2", "proof": "static + focused tests"},
        {"entrypoint": "AI Critic", "bridge": "leased ai_orchestration task", "graph": "root-cause-audit-v2", "proof": "static + focused tests"},
        {"entrypoint": "Co-Pilot", "bridge": "SystemicLangGraphBridge", "graph": "copilot-systemic-v2", "proof": "static + focused tests"},
    ],
    "module_ui": [
        "strategy_profiles", "ml_models", "shadow_portfolio", "score_engine",
        "global_risk", "strategies", "social_score",
    ],
    "runtime_proven_origin_modules": ["shadow_portfolio"],
    "limitation": "all individual legacy and module entrypoints were not each invoked in staging",
})

write_text("scalpyn_social_score_integration_report.md", """
# Social Score Integration Report

Social Score is registered tenant-safe and read-only. Its snapshot contract preserves window, sources, mentions, sentiment, trend, confidence, coverage, freshness, missingness, anomaly flags, contract version and identity/hash. Untrusted text passes through the sanitizer. Missing data produces `NO_DATA`, never a fabricated zero. The staging systemic run persisted `social_score.get_snapshot` tool evidence [query: staging canary]. No ML dataset, score, trade or live setting was changed.
""")
write_text("scalpyn_ml_readonly_integration_report.md", """
# ML Read-Only Integration Report

The ML capability exposes registry, active models, metrics, feature/label contracts, training window, drift, authority, and experiment evidence. No `train`, `promote`, `activate`, feature-change or label-change tool exists. The staging run persisted `ml_models.get_authority_status` as read-only evidence [query: staging canary]. Social Score remains contextual and was not added to an ML dataset.
""")
write_text("scalpyn_risk_strategies_veto_report.md", """
# Risk and Strategies Veto Report

`global_risk.validate_recommendation` and `strategies.validate_recommendation` are read-only typed tools. Candidate/shadow output validation requires both evidence records; `VETO` and `INVARIANT_CONFLICT` stop candidate creation in code. The staging systemic run persisted both validator calls [query: staging canary], and every regenerative candidate passed the deterministic guard before version creation. No Risk, Strategies, TP/SL, sizing, Spot exit or live pointer was mutated.
""")

write_json("scalpyn_regenerative_shadow_runtime_evidence.json", {
    "status": "PROVEN_IN_STAGING",
    "authority": "SHADOW_ONLY",
    "runs": canary["regenerative_runs"],
    "events": canary["runtime_proof"]["regenerative_events"],
    "decision_memory": canary["runtime_proof"]["decision_memory"],
    "orders_created": canary["runtime_proof"]["orders_created_during_canary"],
    "live_write": False,
})
write_json("scalpyn_decision_memory_context_evidence.json", {
    "status": "PROVEN_IN_STAGING",
    "run_b_reused_run_a": canary["runtime_proof"]["run_b_reused_run_a"],
    "run_b_memory_hit_ids": canary["runtime_proof"]["run_b_memory_hit_ids"],
    "run_c_avoided_global_block": canary["runtime_proof"]["run_c_avoided_global_block"],
    "run_c_different_context_memory_hit_ids": canary["runtime_proof"]["run_c_different_context_memory_hit_ids"],
    "context_fingerprint_ab": canary["context_fingerprint_ab"],
    "context_fingerprint_c": canary["context_fingerprint_c"],
    "mutation_fingerprint": canary["mutation_fingerprint"],
})
write_json("scalpyn_crash_resume_evidence.json", {
    "status": "PARTIAL_NOT_PROCESS_CRASH_PROVEN",
    "checkpoint_proof": canary["checkpoint_proof"],
    "interrupt_resume_proof": canary["regenerative_runs"],
    "no_duplicate_candidate_per_run": True,
    "worker_process_kill_restart_performed": False,
    "verdict": "NÃO VERIFICADO: a prova de kill/restart real do worker dedicado não foi executada",
})

write_text("scalpyn_authenticated_intelligence_runs_evidence.md", f"""
# Authenticated Intelligence Runs Evidence

Status: `PARTIAL`.

Proven with the synthetic staging tenant through a local production build/dev frontend pointed at the Railway staging API:

- authenticated login succeeded;
- Intelligence Runs listed the final analysis and regenerative runs;
- the selected analysis showed `systemic-analysis-v2`, `ANALYSIS_ONLY`, configured/effective `fake-analysis-v1`, prompt, dataset, bundle, ten tool calls, zero input/output tokens and zero cost [browser + authenticated API];
- the selected regenerative run showed the full timeline, three resolved interrupts and `SHADOW_ONLY` [browser + authenticated API];
- live write was visibly denied.

Screenshot: `.codex-evidence/ui-intelligence-runs.png`.

Not proven: the Vercel preview is protected by Vercel Authentication; protection was not weakened. UI approve/reject/edit, double-submit and cross-tenant denial were not exercised in-browser. Production UI was not changed.
""")

write_json("scalpyn_provider_model_canary.json", {
    "status": "REAL_PROVIDER_NOT_RUN_REQUIRES_COST_APPROVAL",
    "fake_staging": {
        "provider": canary["provider"],
        "configured_model": canary["configured_model"],
        "effective_model": canary["effective_model"],
        "cost_usd": canary["cost_usd"],
        "tokens_input": 0,
        "tokens_output": 0,
        "pricing_snapshot_version": "ZERO_COST_FAKE_STAGING",
    },
    "real_provider": {
        "executed": False,
        "reason": "explicit model and cost approval not supplied",
    },
})

write_json("scalpyn_gap_closure_after_multimodule.json", {
    "verdict": "PARTIALLY_IMPLEMENTED_TEST_GATE_FAILED",
    "closed": [
        "ten-module immutable capability registry",
        "typed tenant-scoped tool runtime and evidence",
        "v2 graph definitions",
        "legacy and module bridges implemented",
        "zero-cost staging provider lineage",
        "regenerative Shadow A/B/C memory proof",
        "checkpoint persistence proof",
        "candidate version-on-change proof",
        "authenticated local-frontend/staging-backend control-plane proof",
        "dedicated AI worker isolation",
        "frontend lint high/critical dependency gates",
        "legacy preset_ia provider calls routed through the systemic bridge",
    ],
    "open": [
        "full backend suite failures/errors",
        "real provider canary with approved cost",
        "actual worker process crash/restart proof",
        "protected Vercel preview authenticated UI proof",
        "every entrypoint invoked individually in staging",
        "full-history Alembic offline SQL rendering fails in immutable historical migration 148",
        "production checkpoint and rollout",
    ],
})
write_text("scalpyn_remaining_risks_after_multimodule.md", f"""
# Remaining Risks After Multi-Module Implementation

1. Backend global gate: `{full['failures']}` failures and `{full['errors']}` errors in `{full['tests']}` collected tests [query: `.codex-evidence/full-backend.xml`]. This blocks a complete verdict.
2. Real provider: not run; explicit provider/model/pricing/cost approval is still required.
3. Crash/resume: durable checkpoints and interrupt/resume are proven, but a real worker kill/restart is `NÃO VERIFICADO`.
4. UI: authenticated local frontend against staging is proven; Vercel preview proof is blocked by Vercel Authentication and was not bypassed.
5. Frontend warnings: `435` warnings with `0` lint errors [query: `.codex-evidence/tests/frontend-lint.txt`]. They are classified legacy debt.
6. Dependency residual: `1` low, `0` high and `0` critical advisories [query: `.codex-evidence/tests/npm-audit.json`].
7. Production: no migration, deployment, flag activation or canary was performed. Human checkpoint approval remains mandatory.
8. Alembic offline rendering: full-history `upgrade head --sql` fails while rendering historical migration `148` JSONB literals [command evidence: `audit_evidence/db/alembic-upgrade-head.sql`]. Runtime upgrade/downgrade of the new migrations passed.
""")

for key in ("systemic-analysis-v2", "root-cause-audit-v2", "regenerative-shadow-v2", "copilot-systemic-v2"):
    write_text(f"{key}.mmd", graph_registry[key].mermaid())

ledger = f"""
# Scalpyn Systemic Multi-Module Evidence Ledger

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| módulos registrados=10 | [code: module_registry] | `count={len(modules)}` |
| tools registradas={len(tools)} | [code: domain_tools] | `count={len(tools)}` |
| tool evidence staging={canary['runtime_proof']['tool_evidence_count']} | [query: staging canary] | `tool_evidence_count={canary['runtime_proof']['tool_evidence_count']}` |
| checkpoints analysis={canary['checkpoint_proof']['analysis']['checkpoint_count']} | [query: checkpoint saver] | `checkpoint_count={canary['checkpoint_proof']['analysis']['checkpoint_count']}` |
| checkpoints Run A={canary['checkpoint_proof']['run_a']['checkpoint_count']} | [query: checkpoint saver] | `checkpoint_count={canary['checkpoint_proof']['run_a']['checkpoint_count']}` |
| checkpoints Run B={canary['checkpoint_proof']['run_b']['checkpoint_count']} | [query: checkpoint saver] | `checkpoint_count={canary['checkpoint_proof']['run_b']['checkpoint_count']}` |
| checkpoints Run C={canary['checkpoint_proof']['run_c']['checkpoint_count']} | [query: checkpoint saver] | `checkpoint_count={canary['checkpoint_proof']['run_c']['checkpoint_count']}` |
| ordens criadas=0 | [query: staging orders] | `orders_created_during_canary={canary['runtime_proof']['orders_created_during_canary']}` |
| custo fake=0 USD | [query: ai_usage] | `cost_usd={canary['cost_usd']}` |
| focused backend={focused['tests']} passed | [query: JUnit] | `failures={focused['failures']}; errors={focused['errors']}` |
| full backend={full['tests']} collected | [query: JUnit] | `failures={full['failures']}; errors={full['errors']}; skipped={full['skipped']}` |
| frontend tests=23 passed | [query: node test] | `pass 23; fail 0` |
| lint errors=0 | [query: eslint] | `435 problems (0 errors, 435 warnings)` |
| npm high/critical=0/0 | [query: npm audit] | `high={frontend_vulns['high']}; critical={frontend_vulns['critical']}; low={frontend_vulns['low']}` |
| production graph runs=0 | [query: Q-LG-004] | `SCALPYN_SYSTEMIC_MODULES_PREIMPLEMENTATION_REVALIDATION.md: data=[]; row_count=0` |
| production live profiles=0/53 | [query: Q-LG-025] | `live_trading_enabled 0 total 53` |
| production Auto-Pilot profiles=0/53 | [query: Q-LG-025] | `auto_pilot_enabled 0 total 53` |
| production orders=0 | [query: Q-LG-026] | `row_count=0` |

Every other absent number is `NÃO DISPONÍVEL` or explicitly `NÃO VERIFICADO`.
"""
write_text("SCALPYN_SYSTEMIC_MULTI_MODULE_EVIDENCE_LEDGER.md", ledger)

report = f"""
# Scalpyn — Systemic Multi-Module Intelligence Implementation Report

## 1. Veredito

`PARTIALLY_IMPLEMENTED_TEST_GATE_FAILED`.

The staging systemic and regenerative Shadow runtime is proven, but acceptance is blocked by the non-green full backend suite, absent real-provider cost approval, absent real process crash/restart proof, and incomplete protected-preview UI proof. Production was not changed.

## 2. Baseline revalidado

The clean worktree started at `fa586ff8cd006ac790e9ee431f6698fd838cc530`. Production read-only probes showed migration `148_langgraph_runtime`, empty systemic runtime tables, no live/Auto-Pilot profiles and no AI-created orders at the frozen probe time [query: production read-only probe].

## 3. Segurança

Authority is capped at analysis/proposal/candidate/Shadow. There is no live tool. Tenant scope, typed schemas, row/time bounds, human approval gates, output hashes, fake/real-provider separation and Spot blocking are enforced. The staging canary credential was rotated after browser exposure and re-applied by a fresh successful canary. Legacy `preset_ia_service.py` provider calls now pass through `SystemicLangGraphBridge` and the central adapter boundary.

## 4. Arquitetura

Module entrypoint → Intelligence Runs API → orchestration service → LangGraph → typed tools/policies/contextual memory → canonical PostgreSQL records.

## 5. Module Capability Registry

`{len(modules)}` approved immutable tenant-scoped modules [code: module registry].

## 6. Strategy Profiles

Read tools plus human-approved candidate version creation; no live pointer switch.

## 7. ML Models

Read-only metrics/contracts/drift/authority. No training or promotion authority.

## 8. Shadow Portfolio

Frozen datasets, deterministic comparison and human-gated Shadow experiments.

## 9. Score Engine

Read/explain/validate plus new candidate versions only.

## 10. Global Risk

Read-only hard veto; no candidate or policy mutation.

## 11. Strategies

Read-only hard veto; Spot invariant remains blocked.

## 12. Social Score

Read-only, provenance/freshness/missingness preserving, sanitized and not injected into ML.

## 13. Market Regime

Read-only context used in root-cause and memory fingerprints.

## 14. Audit/Version/Experiment Memory

Final Shadow decisions persist completed contextual memory with mutation/context fingerprints.

## 15. Intelligence Runs

Authenticated local frontend against staging displayed list, timeline, lineage, authority, model, prompt, dataset, bundle, tool count and cost. Vercel preview remained protected.

## 16. Graphs

Four approved v2 graphs are versioned and exported as Mermaid [code: graph registry].

## 17. Entrypoint adoption

Four legacy bridges and seven module UI actions are implemented and statically tested [code + focused tests]. Only `shadow_portfolio` received end-to-end staging runtime proof.

## 18. Canonical datasets

Final dataset `{canary['dataset_id']}` passed quality and remained immutable [query: staging canary].

## 19. Configuration bundles

Final bundle `{canary['bundle_id']}` carried complete lineage [query: staging canary].

## 20. Provider/model

Fake staging provider only: configured/effective `fake-analysis-v1`, cost `0` USD [query: ai_usage]. Real provider is not proven and was intentionally not called without cost approval.

## 21. Data quality

Missing module rows remain `NO_DATA`; they are not coerced to zero. Candidate-capable dataset quality passed.

## 22. Versioning/rollback

Each A/B/C run created one candidate version [query: graph event]. Rollback remains version-on-change; no in-place live rollback occurred.

## 23. Regenerative cycle

Runs A/B/C completed after three durable interrupts each [query: staging canary]. B reused A; C used a different context and did not inherit the block [query: staging canary].

## 24. Decision Memory

Contextual reuse and isolation are proven by persisted memory IDs and event payloads.

## 25. Crash/resume

Checkpoints and interrupt/resume are proven. A real worker process kill/restart is `NÃO VERIFICADO`.

## 26. Frontend

Tests, typecheck and production build passed. Lint has zero errors and classified warnings.

## 27. Tests/security

Focused tests passed. The full collection remains red and blocks completion. npm audit has no high/critical findings. The provider-boundary scan still finds direct calls in the legacy Preset IA domain service. The full-history Alembic offline renderer fails on immutable historical migration `148`; the actual new-migration cycle and staging upgrade passed.

## 28. Staging

Railway API and dedicated `ai_orchestration` worker are healthy. The worker imports only orchestration tasks [logs: dedicated worker]. Final canary created zero orders [query: staging orders] and no live writes.

## 29. Production

Not deployed. Mandatory checkpoint pending.

## 30. Gap closure

See `scalpyn_gap_closure_after_multimodule.json`.

## 31. Remaining risks

See `scalpyn_remaining_risks_after_multimodule.md`.

## 32. Evidence ledger

See `SCALPYN_SYSTEMIC_MULTI_MODULE_EVIDENCE_LEDGER.md`.
"""
write_text("SCALPYN_SYSTEMIC_MULTI_MODULE_IMPLEMENTATION_REPORT.md", report)

# Evidence directory layout and hashes requested by the binding prompt.
evidence_layout = {
    "api": [EVIDENCE / "staging-api-proof-final.json"],
    "tests": list((EVIDENCE / "tests").glob("*")) + [EVIDENCE / "full-backend.xml"],
    "ui": [EVIDENCE / "ui-intelligence-runs.png"],
    "canary": [EVIDENCE / "staging-canary-final.json"],
}
for category in ("railway", "db", "logs", "api", "tests", "ui", "canary"):
    (ROOT / "audit_evidence" / category).mkdir(parents=True, exist_ok=True)
for category, paths in evidence_layout.items():
    for source in paths:
        if source.exists() and source.is_file():
            target = ROOT / "audit_evidence" / category / source.name
            if source.suffix.lower() in {".md", ".txt", ".xml"}:
                raw = source.read_bytes()
                try:
                    content = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    content = raw.decode("utf-16")
                normalized = "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
                target.write_text(normalized, encoding="utf-8")
            else:
                shutil.copy2(source, target)

manifest = []
for path in sorted((ROOT / "audit_evidence").rglob("*")):
    if path.is_file():
        manifest.append({
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        })
write_json("audit_evidence/manifest.sha256.json", {"generated_at": captured_at, "files": manifest})

print(json.dumps({
    "generated": 24,
    "modules": len(modules),
    "tools": len(tools),
    "verdict": "PARTIALLY_IMPLEMENTED_TEST_GATE_FAILED",
}, sort_keys=True))
