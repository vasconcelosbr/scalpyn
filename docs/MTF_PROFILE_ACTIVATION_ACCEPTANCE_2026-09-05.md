# Pendências de aceite — atualização dos profiles MTF existentes

## Estado

- O fluxo governado atualiza somente os IDs existentes de L1 e L2.
- A prévia é somente leitura; a aplicação usa uma única transação para profiles, versões, watchlists, contrato e snapshot de rollback.
- O JSON também congela os vínculos atuais das duas watchlists. Uma associação alterada depois da geração causa conflito em vez de ser sobrescrita.
- A aplicação exige execução de calibração `PASSED`; conteúdo declarado pelo JSON não substitui o registro do servidor.
- O contrato final permanece `SHADOW` e `operational_effect:false`.
- A decisão observacional não autoriza compra. O efeito operacional autorizado é exclusivamente a redução do universo pelas watchlists L1/L2.

## Gate estatístico

A proposta está em `docs/MTF_CALIBRATION_POLICY_PROPOSAL_v1.json` com `approval_confirmed:false`.

Ela não foi persistida nem aprovada. O JSON final `PROFILE_MTF_ATUALIZAR_L1_L2_EXISTENTES_SHADOW_v1.json` será emitido pelo script `backend/scripts/build_mtf_activation_document.py` somente quando a execução registrada estiver em `PASSED`.

## Evidência de disponibilidade

Fonte: auditoria de produção somente leitura, capturada em `2026-09-05T18:30:24.457048-03:00`.

| Medida | Origem | Valor literal |
|---|---|---:|
| Histórico governado 15m | `[query] mtf_indicators` | 390 snapshots; 65 símbolos; 2026-09-05T16:45:05.249021+00:00 a 2026-09-05T21:15:41.574352+00:00 |
| Histórico governado 1h | `[query] mtf_indicators` | 260 snapshots; 65 símbolos; 2026-09-05T16:48:28.730637+00:00 a 2026-09-05T21:01:18.842193+00:00 |
| Policies MTF ativas | `[query] mtf_policies` | 0 |
| Runs MTF | `[query] mtf_runs` | 0 |
| Margem 15m proposta | `[calc] ceil(p95 - duração)` | ceil(1894.611329 - 900) = 995 s |
| Margem 1h proposta | `[calc] ceil(p95 - duração)` | ceil(7278.842193 - 3600) = 3679 s |

Conclusão: a infraestrutura está apta a coletar, mas a janela point-in-time governada ainda é insuficiente para executar a calibração proposta. Aprovar a política autoriza o cadastro da regra estatística; não transforma a amostra atual em suficiente e não ativa profiles.

## Implementação entregue

- O scanner das watchlists L1/L2 carrega indicadores por `symbol + market_type + timeframe + scheduler_group` e valida a identidade governada antes de avaliar o profile.
- A validação preserva as duas representações suportadas de origem do funil e confirma explicitamente a forma usada em produção: watchlist `POOL → L1 → L2`.
- O motor de profile entra em modo temporal estrito para `MTF_LAYER`: dado ausente, vencido ou pertencente a outro timeframe reprova a condição, sem fallback para o conjunto plano legado.
- A prévia `POST /api/profiles/mtf/activation-preview` não grava. A aplicação `POST /api/profiles/mtf/activate-existing` exige compare-and-swap de profiles e vínculos, run `PASSED` no servidor e executa profiles, versões, watchlists, contrato e auditoria em uma transação.
- A aplicação preserva os IDs dos profiles, gera novas versões, associa L1 à watchlist L1 e L2 à watchlist L2 e materializa o contrato `SHADOW` com `operational_effect:false`.
- O editor genérico preserva `is_active` e recusa alteração de `MTF_LAYER`. A exclusão física recusa profiles com associação ou histórico e não encerra nem apaga Shadows.
- O rollback restaura as versões, metadados, associações e contrato exatos do snapshot anterior, sem apagar evidências históricas.

## Verificação local

| Verificação | Origem | Resultado literal |
|---|---|---|
| Testes focados MTF/backend | `[test] pytest` | `72 passed in 2.70s` |
| Testes frontend | `[test] npm test` | `tests 83; pass 83; fail 0` |
| Build frontend | `[build] npm run build` | `Compiled successfully`; `Finished TypeScript`; `44/44` páginas |
| Cabeça Alembic | `[query] alembic heads` | `218_mtf_profile_activation_audit (head)` |
| Migração isolada | `[test] PostgreSQL local temporário` | upgrade `217 → 218`, downgrade `218 → 217` e novo upgrade concluídos |

Os testes de integração que dependem de API/PostgreSQL locais foram mantidos como pendência ambiental quando esses serviços não estavam disponíveis; não foram reclassificados como sucesso.

## Rollback

1. Chamar `POST /api/profiles/mtf/activation/{audit_id}/rollback`.
2. O endpoint rejeita o rollback se algum profile, vínculo de watchlist ou contrato tiver mudado após a ativação.
3. Na mesma transação, ele reativa as versões exatas registradas em `before_snapshot`, restaura metadados/configs, devolve os vínculos L1/L2 e restaura o contrato anterior.
4. O cache `spot_engine` é invalidado antes e depois do commit; falha pós-commit é retornada explicitamente.
5. Versões, Shadows e decisões são preservados, e o registro recebe `ROLLED_BACK`, ator e horário.

## Ledger de evidências

| Número reportado | Origem | Valor literal da fonte |
|---|---|---|
| 390 | `[query] mtf_indicators` | `governed_snapshots: 390` |
| 260 | `[query] mtf_indicators` | `governed_snapshots: 260` |
| 65 | `[query] mtf_indicators` | `symbols: 65` em ambos os timeframes |
| 0 policies | `[query] mtf_policies` | `[]` |
| 0 runs | `[query] mtf_runs` | `[]` |
| 995 s | `[calc] ceil(1894.611329 - 900)` | p95 e duração acima |
| 3679 s | `[calc] ceil(7278.842193 - 3600)` | p95 e duração acima |
| 72 | `[test] pytest` | `72 passed in 2.70s` |
| 83 | `[test] npm test` | `tests 83; pass 83; fail 0` |
| 44 | `[build] npm run build` | `Generating static pages ... (44/44)` |
