# RELATÓRIO — Fix `no_hollow_ai_reviews_24h` / Safety Guard

Data: 2026-06-28 (America/Sao_Paulo)

## 1. Resumo executivo

O incidente foi corrigido e implantado. Os 3 reviews hollow que estavam na janela de 24h foram identificados como artefatos pré-fix, preservados integralmente em snapshot e reclassificados de `COMPLETED` para `LEGACY_HOLLOW_REVIEW`, sem `DELETE`. O endpoint agora retorna `safety_status=PASS`, `hollow_ai_reviews_24h=0` e `legacy_hollow_reviews_24h=3`.

Um novo review real foi concluído com `tokens_input=255`, `tokens_output=868`, modelo e resumo persistidos. O banco também possui trigger fail-closed que rejeita qualquer novo `COMPLETED` sem o contrato completo.

Veredito: `SAFETY_PASS_WITH_LEGACY_AI_REVIEW_WARNINGS`.

## 2. Evidência inicial

A evidência inicial disponível foi o erro textual informado pelo usuário: `404 — This page could not be found`, seguido pelo safety guard indicando `no_hollow_ai_reviews_24h=3`. Não havia screenshot anexado. A query de preflight confirmou literalmente `3` hollows na janela.

A rota do dashboard é `/profile-intelligence`, aba **Calibration Evolution**. Não existe uma rota separada para essa aba.

## 3. Reviews hollow identificados

| review_id | requested_at UTC | completed_at UTC | old_status | tokens_in | tokens_out | new_status | classificação |
|---|---|---|---|---:|---:|---|---|
| `801966a9-2d2f-44d5-b7f3-1ecc924d09db` | 2026-06-27 04:58:24 | 2026-06-27 04:58:24 | COMPLETED | 0 | 0 | LEGACY_HOLLOW_REVIEW | pré-fix / legacy |
| `eec32b85-3191-4519-8ebc-279366ddaa56` | 2026-06-27 09:03:15 | 2026-06-27 09:03:15 | COMPLETED | 0 | 0 | LEGACY_HOLLOW_REVIEW | pré-fix / legacy |
| `0021d049-30e4-4c88-ae3b-56144534353c` | 2026-06-27 13:03:40 | 2026-06-27 13:03:40 | COMPLETED | 0 | 0 | LEGACY_HOLLOW_REVIEW | pré-fix / legacy |

Existe um quarto hollow histórico (`026e02bc-4bff-4eb5-80a2-c72b898b4245`, 00:53:56Z), mas ele já estava fora da janela móvel de 24h e não pertence ao escopo literal desta correção. O runner aplica exatamente `(requested_at >= now()-24h OR created_at >= now()-24h)`.

## 4. Classificação temporal e causa-raiz

O deploy que introduziu a primeira proteção contra hollows (`3b30a8438902b12b0d9beadf61c42c662ce58f2f`) iniciou às 15:33:27Z e estava executando no worker às 15:35:23Z. Os 3 registros do incidente são anteriores a essa fronteira. O primeiro review real posterior foi concluído às 15:41:37Z.

Causa-raiz histórica: o writer podia persistir `COMPLETED` sem tokens/resumo e não persistia corretamente o modelo. A proteção anterior corrigiu o caminho principal; esta entrega fecha as lacunas restantes com contrato centralizado, validação imediatamente antes do update e trigger de banco.

## 5. Implementação

- `backend/app/services/ai_review_safety_service.py:10`: contrato centralizado de review real.
- `backend/app/services/profile_intelligence_live_service.py:729`: validação fail-closed imediatamente antes da persistência.
- `backend/app/services/profile_intelligence_live_service.py:762`: `completed_at` explícito no update.
- `backend/alembic/versions/116_ai_review_hollow_safety.py:34`: função/trigger que rejeita `COMPLETED` inválido.
- `backend/alembic/versions/116_ai_review_hollow_safety.py:18`: tabela auditável de snapshots.
- `backend/alembic/versions/117_ai_review_audit_table_name.py:19`: alinhamento para `profile_ai_reviews_reclassification_audit`.
- `backend/scripts/reclassify_hollow_ai_reviews.py:28`: escopo literal de 24h.
- `backend/scripts/reclassify_hollow_ai_reviews.py:50`: snapshot antes do update.
- `backend/scripts/reclassify_hollow_ai_reviews.py:75`: evento `AI_REVIEW_RECLASSIFIED`.
- `backend/app/api/calibration_evolution.py:611`: endpoint safety ampliado.
- `frontend/app/profile-intelligence/page.tsx:1209`: banner `Safety Guard — PASS/FAIL`.
- `frontend/app/profile-intelligence/page.tsx:1223`: legacy como warning informativo.
- `backend/tests/test_ai_review_hollow_safety.py:14`: testes obrigatórios.

## 6. Reclassificação e preservação

Dry-run final: `count=3`, todos com destino `LEGACY_HOLLOW_REVIEW`.

Resultado SQL pós-transação:

```text
total_reviews=7 (antes dos dois disparos novos)
snapshot_rows=3
legacy_24h=3
hollow_completed_24h=0
activity_rows=3
```

As duas tentativas intermediárias que encontraram incompatibilidade de tipos asyncpg foram integralmente revertidas. A prova antes da tentativa final foi `snapshots=0 | still_completed=3 | activity_rows=0`.

## 7. Endpoint safety final

Resposta literal da função FastAPI contra o banco de produção:

```json
{
  "safety_pass": true,
  "safety_status": "PASS",
  "no_hollow_ai_reviews_24h": true,
  "hollow_ai_reviews_24h": 0,
  "invalid_completed_ai_reviews_24h": 0,
  "legacy_hollow_reviews_24h": 3,
  "failed_ai_reviews_24h": 1,
  "last_real_ai_review": {
    "id": "9b8e6739-7ead-4be6-9a91-9fe66714f810",
    "status": "COMPLETED",
    "model_name": "claude-haiku-4-5-20251001",
    "tokens_input": 255,
    "tokens_output": 868
  }
}
```

O `failed_ai_reviews_24h=1` corresponde ao disparo controlado que recebeu 401 da chave de ambiente e foi corretamente persistido como `FAILED_AI_CALL`, sem hollow. O dry-run seguinte confirmou a chave DB validada e o novo review concluiu com HTTP 200.

## 8. Activity Timeline

| UTC | Evento | Evidência |
|---|---|---|
| 2026-06-27 04:58:24 | hollow #1 | SQL |
| 2026-06-27 09:03:15 | hollow #2 | SQL |
| 2026-06-27 13:03:40 | hollow #3 | SQL |
| 2026-06-27 15:35:23 | fix anterior executando no worker | Railway log |
| 2026-06-27 15:41:37 | primeiro review real pós-fix | SQL |
| 2026-06-28 | 3 snapshots + 3 reclassificações | SQL / activity log |
| 2026-06-28 03:41:11 | chave env inválida → `FAILED_AI_CALL` | runner / SQL |
| 2026-06-28 03:42:20 | review DB-key → `COMPLETED`, 255/868 tokens | runner / SQL |

## 9. Testes

```text
backend AI safety + regressão: 19 passed
frontend: 19 passed
TypeScript: PASS (tsc --noEmit)
Next.js production build: PASS (41 rotas)
Alembic head: 117_ai_review_audit_name
Python py_compile: PASS
```

A suíte backend completa não chegou à execução por erro preexistente de coleta: `test_migration_023_taker_ratio_scale.py` referencia `backend/alembic/versions/023_taker_ratio_scale_v2.py`, arquivo ausente no repositório. As suítes diretamente afetadas passaram integralmente.

## 10. Deploy

| Componente | Commit/deployment | Status |
|---|---|---|
| Código principal | `5e38767c0d29657ddcb77a0adb12e0ae0ed93ece` | publicado |
| Correções do runner e escopo | até `4e544e8` | publicado em `main` |
| Railway backend | `2f2eee00-2ec3-450b-9b61-9ef05ace78dd` | SUCCESS |
| Railway worker-compute | `2177bda1-2fa5-45ab-ad01-a3b3a6c30126` | SUCCESS |
| Vercel production | `dpl_13AmHRu68rMFHnE8cB7BA3RSdZ1c` | Ready |
| Frontend URL | `https://frontend-ecru-eight-91.vercel.app` | production alias |

## 11. UI e screenshots

A build e o teste confirmam a lógica: com `safety_pass=true` o banner é verde, mostra `Safety Guard — PASS`, não renderiza o alerta vermelho e exibe `legacy_hollow_reviews_24h=3` como aviso amarelo informativo.

Screenshots inicial e final não foram capturados: os runtimes Browser, Chrome e Computer Use falharam antes da navegação com o mesmo erro de infraestrutura (`sandboxPolicy` ausente). Essa limitação afeta somente o artefato visual; Vercel está `Ready` e a lógica compilada/testada corresponde ao payload real do endpoint.

## 12. Safety final

Transação `READ ONLY` seguida de rollback:

```text
live_enabled=0
total_profiles=109
profiles_created_24h=0
possible_live_orders=0
active_new_models_24h=0
production_mutations_24h=0
ML_GATE_ENABLED=false
hollow_ai_reviews_24h=0
invalid_completed_contract_24h=0
legacy_hollow_reviews_24h=3
failed_ai_reviews_24h=1
```

## 13. Checklist

| Contrato | Status | Evidência |
|---|---|---|
| Hollow reviews identificados | PASS | SQL: 3 IDs |
| Snapshot criado | PASS | SQL: 3 |
| Reclassificação sem delete | PASS | SQL: 7 preservados antes dos novos disparos; 3 snapshots |
| Safety check ignora legacy/failed | PASS | endpoint + testes |
| Novos COMPLETED exigem tokens | PASS | trigger + 19 testes + SQL |
| Novo AI review real | PASS | SQL: 255/868 tokens |
| UI sem alerta vermelho indevido | PASS lógico / visual indisponível | build + teste + payload; screenshot bloqueado pelo runtime |
| Activity Timeline registra correção | PASS | SQL: 3 eventos |
| Safety final | PASS | SQL read-only |

## 14. Ledger de evidências

| Afirmação | Origem | Valor literal |
|---|---|---|
| Hollows iniciais 24h | SQL preflight | `3` |
| Hollows finais 24h | SQL / endpoint | `0` |
| Contratos COMPLETED inválidos 24h | SQL / endpoint | `0` |
| Legados 24h | SQL / endpoint | `3` |
| Snapshots | SQL | `3` |
| Eventos de reclassificação | SQL | `3` |
| Reviews totais preservados antes dos novos disparos | SQL | `7` |
| Novo review real | SQL | `9b8e6739-7ead-4be6-9a91-9fe66714f810` |
| Tokens do novo review | SQL / endpoint | `255 / 868` |
| Modelo | SQL / endpoint | `claude-haiku-4-5-20251001` |
| Safety endpoint | resposta da função | `PASS` |
| Profiles | SQL | `109` |
| Live enabled | SQL | `0` |
| Live orders possíveis | SQL | `0` |
| Novos modelos ativos 24h | SQL | `0` |
| Profiles criados 24h | SQL | `0` |
| Mutações production 24h | SQL | `0` |
| ML Gate | Railway variable | `false` |
| Testes backend afetados | pytest | `19 passed` |
| Testes frontend | node:test | `19 passed` |
| Railway backend/worker | deployment list | `SUCCESS / SUCCESS` |
| Vercel | inspect | `Ready` |

## 15. Veredito

```text
SAFETY_PASS_WITH_LEGACY_AI_REVIEW_WARNINGS
```

Motivo: o sistema está seguro e sem hollows `COMPLETED` na janela; os 3 artefatos pré-fix permanecem preservados como `LEGACY_HOLLOW_REVIEW` e aparecem somente como warning informativo.