
# Remaining Risks After Multi-Module Implementation

1. Backend global gate: `68` failures and `12` errors in `1606` collected tests [query: `.codex-evidence/full-backend.xml`]. This blocks a complete verdict.
2. Real provider: not run; explicit provider/model/pricing/cost approval is still required.
3. Crash/resume: durable checkpoints and interrupt/resume are proven, but a real worker kill/restart is `NÃO VERIFICADO`.
4. UI: authenticated local frontend against staging is proven; Vercel preview proof is blocked by Vercel Authentication and was not bypassed.
5. Frontend warnings: `435` warnings with `0` lint errors [query: `.codex-evidence/tests/frontend-lint.txt`]. They are classified legacy debt.
6. Dependency residual: `1` low, `0` high and `0` critical advisories [query: `.codex-evidence/tests/npm-audit.json`].
7. Provider boundary: legacy `preset_ia_service.py` still contains direct Anthropic calls [scan: `audit_evidence/tests/direct-provider-scan.txt`]. Adapters and key/catalog validation are allowed, but this domain service remains a blocker.
8. Production: no migration, deployment, flag activation or canary was performed. Human checkpoint approval remains mandatory.
9. Alembic offline rendering: full-history `upgrade head --sql` fails while rendering historical migration `148` JSONB literals [command evidence: `audit_evidence/db/alembic-upgrade-head.sql`]. Runtime upgrade/downgrade of the new migrations passed.
