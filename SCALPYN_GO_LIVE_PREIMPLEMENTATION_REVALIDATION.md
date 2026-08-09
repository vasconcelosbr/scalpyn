# Scalpyn go-live preimplementation revalidation

Status: `FASE_0_COMPLETE_STAGING_PROVIDER_KEY_REQUIRED`.

Data da revalidação: `2026-08-09` `[runtime]`.

## Fonte e worktree

- checkout original: HEAD `fc53f543c952de4b0799514f76fe7cfc80e27406` `[git]`, com `54` entradas dirty `[git status --porcelain]`; nenhuma foi alterada por esta execução;
- worktree isolado: branch `codex/langgraph-production-go-live-20260809` `[git]`;
- evidence commit auditado: `0fb6ca1306fef8b723091146798e3aa2ad8b5332` `[git]`;
- application commit desta preparação: `b03a4adb528925b5f8997c25bdeefde66e93cfe3` `[git]`;
- `origin/main`: `6c4020c958b9ab8c8e3edf5d5cffa2b8072f39d8` `[git]`;
- relação antes do novo commit: `0` behind e `32` ahead `[git rev-list]`;
- worktree isolado após o commit: clean `[git status --short]`;
- archive imutável de `b03a4ad`: SHA-256 `98272bb5727975e628915f12fd89669bf20696f093b23fd6e7f224c1968700a9` `[git archive + Get-FileHash]`.

O commit `0fb6ca...` é o evidence commit. O código executável final para staging é `b03a4ad...`; a diferença executável é a remediação dos gates de catálogo/budget e os probes operacionais, não uma divergência não auditada.

## Schema e serviços

- Alembic staging: `150_multimodule_hardening` `[query: alembic_version]`;
- Alembic production: `150_multimodule_hardening` `[query: alembic_version]`;
- staging schema health: `schema_ok=true`, `checked_count=32`, `missing=[]` `[HTTP /api/health/schema]`;
- staging API deployment `6b8c8669-4a7b-401e-abee-9489207bae2c`, image `sha256:db54af8fc78163ba3cfe35dc3efefd6e3b34a1bf691a27404aaea7a18c7a7c12`, status `SUCCESS` `[Railway]`;
- staging worker deployment `d5c79002-5fdf-45d6-8648-11d0ed874c09`, image `sha256:17fdfbeea2b5f88a632a2748c0b2c8b35c3d0334981b473498a66866bbef9f12`, status `SUCCESS` `[Railway]`;
- staging Vercel preview `dpl_EyyJNpocffjKkDpJ9w924AGxosZe`, status `READY` `[Vercel]`;
- production API deployment `4ff3c107-2431-4427-84b1-5ee91f00fdeb`, image `sha256:7fa922484fcdd4baac907d72599ab44c25bc19c4f487de2e9d3124cf1001bcc8`, status `SUCCESS` `[Railway]`;
- production worker deployment `7e307ac1-9f68-49d0-a1a6-85e001f2d7f1`, image `sha256:e41544a0b33aee43a7c3647707f7dd4928193f27e00003b44c6f0b94385a1a28`, status `SUCCESS` `[Railway]`;
- production Vercel deployment `dpl_8GTy7i92msxdsG1irBkEspnfg44d`, target `production`, status `READY` `[Vercel inspect]`.

## Proveniência de build

```text
evidence_commit 0fb6ca...
→ application_commit b03a4ad...
→ clean git archive sha256:98272b...
→ staging API image sha256:db54af...
→ staging worker image sha256:17fdfb...
→ Vercel preview dpl_EyyJNpocffjKkDpJ9w924AGxosZe
```

Os deployments Railway foram submetidos do worktree clean com a mensagem contendo `b03a4ad`; o preview Vercel foi construído da mesma árvore. Nenhuma promoção para produção foi executada.

## Provider, budget e bridges

- registry local permitido: Anthropic, OpenAI e Gemini `[source: provider_registry.py]`;
- staging provider keys: `0` `[query: ai_provider_keys metadata only]`;
- staging model approvals: `0` `[query: ai_model_approvals]`;
- staging budget policies: `0` `[query: ai_budget_policies]`;
- production possui `1` chave Anthropic ativa e validada para um tenant produtivo não selecionado `[query: metadata only]`; ela não será copiada nem usada em staging;
- bridges legadas: adoção estática `4/4` `[artifact: scalpyn_provider_boundary_closure.json]`; runtime E2E final `0/4` `[query/artifact]`, pendente da fase específica;
- o registro staging rotulado `anthropic` com `17` tokens de entrada e `11` de saída pertence ao fake adapter histórico: `strong_fake_code_path_match=true` `[query + source: systemic_ai_staging_canary.py]`; chamadas externas nesse artefato: `0` `[artifact: scalpyn_systemic_ai_staging_canary.json]`.

## Flags e reconciliação de segurança

- staging `LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED=false` em API e worker `[Railway Variables]`;
- staging `AI_MODULE_SHADOW_PORTFOLIO_ENABLED=false` em API e worker `[Railway Variables]`;
- staging orders `0`, live profiles `0`, Auto-Pilot profiles `0` `[query]`;
- production orders `0`, live profiles `0`, Auto-Pilot profiles `0` `[query]`;
- produção não foi alterada nesta fase `[deployment readback]`.

## Testes atuais

- focados provider/LangGraph: `84 passed` e `0 failed` `[pytest]`;
- frontend: `23 passed` e `0 failed` `[node test]`;
- TypeScript e ESLint do componente alterado: pass `[tsc/eslint]`;
- suíte backend global local: `1588 passed`, `19 failed`, `13 errors` `[pytest]`; as falhas observadas dependem de banco/API local indisponíveis, incluindo `localhost:8001`. Este resultado não é aceito como gate final e deverá ser repetido no ambiente hermético da FASE 5.

## Gate

FASE 0 está concluída. O próximo bloqueador é configurar uma chave Anthropic autorizada diretamente no Railway staging para o tenant sintético inativo `b02e84ad-a0eb-4fca-8cf6-bef1ccaafc40`. Nenhuma geração real poderá ocorrer antes do checkpoint humano de custo.

## Ledger de evidências numéricas

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| dirty original `54` | `[git]` | `original_dirty_count: 54` |
| staging schema `32`, missing `0` | `[HTTP]` | `checked_count: 32; missing: []` |
| provider keys/approvals/budgets staging `0/0/0` | `[query]` | arrays vazios nos três objetos |
| bridges runtime `0/4` | `[query/artifact]` | runtime E2E ainda vazio; required `4` |
| staging safety `0/0/0` | `[query]` | orders `0`; live `0`; Auto-Pilot `0` |
| production safety `0/0/0` | `[query]` | orders `0`; live `0`; Auto-Pilot `0` |
| testes focados `84/84` | `[pytest]` | `84 passed` |
| frontend `23/23` | `[node test]` | `pass 23; fail 0` |
| suíte global local | `[pytest]` | `1588 passed, 19 failed, 13 errors` |
