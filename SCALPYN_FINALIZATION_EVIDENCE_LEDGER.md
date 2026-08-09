# Scalpyn finalization evidence ledger

Verdict: `PARTIALLY_COMPLETE_PROVIDER_NOT_PROVEN`.

## Evidence classes

| ID | Classification | Scope |
|---|---|---|
| E-GIT | `CODE_PROVEN` | worktree, branch and immutable revisions |
| E-CODE | `CODE_PROVEN` | provider boundary and Spot authority |
| E-DB | `SCHEMA_PROVEN` | Alembic and runtime persistence |
| E-TEST | `TEST_PROVEN` | backend, frontend, lint and dependency gates |
| E-API | `STAGING_RUNTIME_PROVEN` | staging health and API runtime |
| E-LOG | `STAGING_RUNTIME_PROVEN` | API/worker lifecycle and restart |
| E-UI | `AUTHENTICATED_UI_PROVEN` | protected Vercel preview |
| E-CANARY | `STAGING_RUNTIME_PROVEN` | fake-provider module, crash and A/B/C runs |
| E-DEPLOY | `STAGING_RUNTIME_PROVEN` | Railway and Vercel deployment identity |
| E-SEC | `TEST_PROVEN` / `NOT_PROVEN` | scans passed; historical revocation remains unproven |

## Numeric evidence

| NÃƒÆ’Ã…Â¡MERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| backend final passed=`1614` | `[query: pytest JUnit]` | `tests=1614; failures=0; errors=0; skipped=0` |
| backend final failed=`0` | `[query: pytest JUnit]` | `failures=0` |
| backend final errors=`0` | `[query: pytest JUnit]` | `errors=0` |
| backend final skipped=`0` | `[query: pytest JUnit]` | `skipped=0` |
| backend warnings=`5` | `[query: pytest stdout]` | `1614 passed, 5 warnings in 64.98s` |
| casos inicialmente nÃƒÆ’Ã‚Â£o aprovados=`83` | `[calc: 71 failures + 12 errors]` | `71 + 12 = 83` |
| frontend tests passed=`23` | `[query: node test]` | `pass 23; fail 0; skipped 0` |
| lint errors=`0` | `[query: eslint]` | `435 problems (0 errors, 435 warnings)` |
| lint baseline warnings=`435` | `[query: eslint]` | `435 problems (0 errors, 435 warnings)` |
| lint warning delta=`0` | `[calc: final 435 - baseline 435]` | `435 - 435 = 0` |
| npm low=`1` | `[query: npm audit JSON]` | `low=1` |
| npm high=`0` | `[query: npm audit JSON]` | `high=0` |
| npm critical=`0` | `[query: npm audit JSON]` | `critical=0` |
| direct provider calls in domain services=`0` | `[query: static boundary scan]` | `direct_domain_calls=0` |
| validated staging provider keys=`0` | `[query: staging DB/env]` | `validated_provider_keys=0` |
| active model approvals=`0` | `[query: staging DB]` | `active_model_approvals=0` |
| active budget policies=`0` | `[query: staging DB]` | `active_budget_policies=0` |
| real provider calls=`0` | `[query: provider canary evidence]` | `status=NOT_RUN` |
| provider real tokens/cost | `[NOT_PROVEN]` | `NÃƒÆ’Ã†â€™O DISPONÃƒÆ’Ã‚ÂVEL` |
| module origins E2E=`7/7` | `[query: staging canary matrix]` | seven rows with `status=COMPLETED` |
| legacy bridges static=`4/4` | `[query: boundary closure]` | `static_adoption=4; total=4` |
| legacy bridges runtime=`0/4` | `[query: boundary closure]` | `runtime_e2e=0; total=4` |
| crash duplicate events=`0` | `[query: staging DB after restart]` | `duplicate_events=0` |
| crash orders=`0` | `[query: staging DB after restart]` | `orders=0` |
| A/B/C completed=`3/3` | `[query: regenerative evidence]` | run A, run B and run C `COMPLETED` |
| authenticated cross-tenant endpoint denials=`3/3` | `[query: deployed UI/API]` | `context=404; interrupts=404; timeline=404` |
| staging AI orders=`0` | `[query: acceptance evidence]` | `orders_created=0` |
| staging live writes=`0` | `[query: acceptance evidence]` | `live_writes=0` |
| production mutations=`0` | `[query: command/deployment ledger]` | `PRODUCTION_MUTATIONS_BEFORE_APPROVAL=0` |
| active secrets in final artifacts=`0` | `[query: gitleaks plus false-positive classification]` | `active_secret_findings=0` |
| historical credential values requiring revocation proof=`3` | `[query: redacted Git-history scan]` | three named development values; values not reproduced |

## SHA-256 evidence manifest

The manifest hashes every file under `final_evidence/` except itself. Its own hash is recorded below to avoid recursive self-hashing.

| PATH | BYTES | SHA-256 |
|---|---:|---|
| canary/final-staging-acceptance.json | 773 | 5992f20774518015d085e773dafaa4418ed2c3e85f160dec5fe4eb3699733918 |
| canary/health-final.json | 53 | 0076e9197d5f78d04cea2268a83116016303c20a51f3ce4b25445a252eebc70f |
| canary/module-origin-e2e-matrix.json | 1132 | 896b2a9ce2e3a9b98aa395e6c7aa728da528c664a7440f12059f64a0407e667a |
| canary/railway-api-deployments.json | 35927 | 74d56ed977f0bba1fd9575fe75cfea675aa3e4420a870837d28db3b41a0ec261 |
| canary/railway-status.json | 149234 | 5df68a2e530b9421cb98729dd41f9ee7ebecb73b1f633c0d6a8e08878953eb2a |
| canary/railway-worker-deployments.json | 20045 | 79f233a36b02b4904ef5a52f6f1ea3373a82ab3ad54fbe95d14bf4921799c952 |
| canary/regenerative-abc-final-evidence.json | 913 | d52dafedf64d5c7ec2249153f6bc79eae8cf2ad2174b0c25ecb316ca52ba1fcf |
| canary/staging-api-proof-final.json | 76895 | c8f8bdaef9e818fbe2f852102a03cf1bee30c146731a491164ff89e5143f608a |
| canary/staging-canary-final.json | 39298 | de400b0e79ab4ec5dfec917ffa393e75b28c478ae1a9397690826bd4e17407fd |
| db/alembic-heads.txt | 34 | 9102d1a1233b95afe55a4330e1fd9f4567d01153fe57e47aa5d42cfa93a212d9 |
| db/alembic-offline-dr-resolution.md | 587 | bc14d5aa3b5141a6c67fa58aea2c16b90bf20db07df2deebe753f1508167eb66 |
| db/alembic-upgrade-head.sql | 304141 | 5779c11f951dc567ba906b7b5161938dc9b841ddd6c9148a39fb003d04034306 |
| db/runtime-persistence-proof.json | 39298 | de400b0e79ab4ec5dfec917ffa393e75b28c478ae1a9397690826bd4e17407fd |
| git/revision.json | 352 | be1161ac3f223a562eb4d05b2814b52aa974b2458854fd413c69893b5aa53d93 |
| provider/direct-provider-classification.md | 492 | e58df37236602418c1046293343b8aec987b52a58694d844f2fcd5325b2ba39f |
| provider/direct-provider-scan.txt | 1528 | 465dd3c278ecba377ab8b9e21b4d3911bf1884b96bca4eddcb117c4ae6c803dd |
| provider/provider-boundary-closure.json | 857 | 1f279acbc650d19080e3a70b6ab70fb87ec80a1ca09e88007964f6fd1e3c7a99 |
| provider/real-provider-staging-canary.json | 981 | 296305d9bca62be46c0b4deb11c0f8e5f83babcdd3926320103486579c0172c3 |
| security/audit-evidence.json | 1142 | c1d78d484eb60bd7142091ae6fc631223dfb2aaa8b6585b0010fd91cb0c1c03b |
| security/canary-credential-incident-closure.md | 1792 | c6c697475a84b875d6e8ae30dd93374087d637259e484f94569ad8dfd27bfed7 |
| security/codex-evidence.json | 3 | 37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570 |
| security/deliverables-rescan.json | 1364 | 3d414b8809b7c63231167cc54eda6ff96b601d2daa58049939902227a80ab680 |
| security/final-evidence.json | 3 | 37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570 |
| security/final-evidence-rescan.json | 1827 | 7eca80e5e619e7d0e2556410c01114499f6259480f6dffadfa287d36b4446340 |
| security/final-secret-scan-summary.json | 761 | 883da321d0f4490abe474afe63c7cff493057ac42ebfc685bd9ee84f23de1028 |
| security/frontend-build.json | 3340 | e09a086c0ea51ebbf2887befd21b7e451e2367df1c45a73731e29a23315b690e |
| security/frontend-build-clean.json | 3340 | d3bb54c90811cbb4ab178cc37bc2ca79ad9f6fa3ee7cd30e0a6eeeeff1959902 |
| security/git-history.json | 54689 | 40da04bba5ee6f575120d176816a007b6bb062895810709df154e87447fed685 |
| security/git-history-relevant.json | 957 | 84f5bd0b7e13331f80104ed5ed7befe8165608ba800e2fd597c1d417fd4f9e2c |
| tests/backend-final-test-results.json | 797 | 034a4f8fdbad8800c1217b2686a1c433b8bbf1ca975b00259e84b44c746d46fd |
| tests/backend-full-after.txt | 346 | f58d68430fb71771eab0782b2cd95d363658996bf119389b198b90633761c585 |
| tests/backend-full-after.xml | 229562 | 74e32079c48a06d46ffd8a96d32e5983c1494203cba7466a2e2cf6d47f839693 |
| tests/backend-full-before.xml | 984221 | 98dc99d2947d216961b920daabbeb14bc00296ca657f9f18ff40829eed63b8e3 |
| tests/backend-full-final.xml | 229562 | 74e32079c48a06d46ffd8a96d32e5983c1494203cba7466a2e2cf6d47f839693 |
| tests/eslint.json | 2026457 | 659b09a59c8a5a974d520d3981824d4b46b064ac2d371fcd19655e9064e19451 |
| tests/failure-classification.json | 46600 | 0ea573afde4ea836ebfe2d2639848490f7e089e7d8253d5ad84d930c86aaea3e |
| tests/focused-backend.txt | 751 | e083412afa5957bf68c1434511f08dfa3d06a884b484ced602379e7608f55ac0 |
| tests/focused-backend.xml | 10588 | 1612ff37cfbf87253f8db9cbb8e2ce5fcef681ce27f7e9752ab376df3b0086d3 |
| tests/frontend-build.txt | 2539 | 185c367115bf0794e6c4c6f4af459027275a336591dd0bb9297b5b7008dee754 |
| tests/frontend-lint.txt | 230018 | 39892f56958fe95fc076fb9ac428977092dc9f3985113d07e922e67ed9f7dceb |
| tests/frontend-tests.txt | 2329 | 38c0ced69e60af8f950e05e55bacc81617ecc6795e539e64050113bc1762f351 |
| tests/git-diff-check.txt | 5566 | 497b713eeb516a91aaf7a6a584811b2667cb26378f9008310320943560fc574f |
| tests/npm-audit.json | 1226 | bbd4ff8232361593ac11fc640817eaeb3d0e2b311bddf3888ca372f00661cf53 |
| tests/pip-check.txt | 31 | 672aec65a45bed50e70229c70934a7eebf0462595c459b83023027645d57cccf |
| ui/authenticated-ui-test-results.json | 883 | e91951aa57f36dbb8cab453fd7f0501866f0dc05f9b3fb4af452d9071d2c440c |
| ui/authenticated-vercel-ui-evidence.md | 2203 | a32dad583750f28462bc8d15074f7d100989e824c1285d2b127181c301b9882a |
| ui/intelligence-runs-authenticated-redacted.jpg | 36584 | e238af815d2dc79d80650520441edfcfec73ba38b446cd8b60f6df57e16a65d1 |
| worker/api-final.jsonl | 11577 | 57a6638b09e0bd5afb955edf15b22d4a8d1b1b5309ebaa0f5fd58588eac59826 |
| worker/crash-resume-runtime-evidence.json | 1085 | e11293c51dbaec2f86d02f505048eda5630b9f82360e14cdf05f6a77cff69de9 |
| worker/worker-final.jsonl | 7322 | 98ab3fdf860de75738d53287c206c85a6bd24e0fb308c765b17e15815b589d3c |
| manifest.sha256.json | 12654 | de00497a232877f7f6cab5c49e21c1966eadb08ce90275f674bd89d17c4a83f3 |
