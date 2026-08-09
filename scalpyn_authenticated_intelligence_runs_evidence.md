
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
