# Scalpyn Systemic Modules — Revalidação Pré-Implementação

## Escopo e autoridade

Esta revalidação precede qualquer alteração funcional solicitada por `PROMPT_IMPLANTACAO_PENDENCIAS_LANGGRAPH_MODULOS_SCALPYN.md`. A autoridade permanece limitada a `ANALYSIS_ONLY`, `PROPOSAL_ONLY`, `CANDIDATE_ONLY` e `SHADOW_ONLY`. Escrita live, ordens reais, promoção automática de modelo e mutação automática de Risk, Strategies, Spot, TP, SL, sizing ou alavancagem permanecem proibidas.

O trabalho ocorre no checkout limpo `codex/systemic-multimodule-langgraph-20260808`, derivado do commit `fa586ff8cd006ac790e9ee431f6698fd838cc530`. O checkout original sujo não foi alterado.

## Baseline de código e dependências

- Alembic possui uma única cabeça: `148_langgraph_runtime` `[query: Q-LG-001, produção, 2026-08-08T22:13:45Z]`.
- Os testes focados de fundação sistêmica e runtime LangGraph concluíram com `66` aprovações `[comando: pytest focado]`.
- A suíte backend completa parou no limite vinculante após `20` falhas, com `278` aprovações `[comando: pytest backend/tests --maxfail=20]`. As falhas observadas incluem contrato legado de migração ausente, fixtures antigas de ML e dependência/conexão de banco; elas serão separadas de regressões desta implementação.
- O import de CatBoost concluiu com sucesso na versão `1.2.10` `[comando: import catboost]`.
- O frontend concluiu `23` testes com `23` aprovações e nenhuma falha `[comando: npm test]`.
- A verificação TypeScript concluiu sem erro `[comando: npx tsc --noEmit]`.
- O build Next.js concluiu com sucesso e enumerou `44` páginas estáticas `[comando: npm run build]`.
- O lint global encontrou `371` erros `[comando: npx eslint . --quiet]`.
- A auditoria de dependências de produção encontrou `4` vulnerabilidades altas e nenhuma crítica `[comando: npm audit --omit=dev --json]`. Os pacotes agregadores reportados são `next`, `postcss`, `sharp` e `nanoid` `[comando: npm audit --omit=dev --json]`.

## Estado de produção — leitura congelada

A inspeção foi executada em transação read-only, com `statement_timeout=30s` e `lock_timeout=3s` `[query: Q-LG-000]`.

- Definições aprovadas presentes: apenas as quatro versões `v1` do runtime atual `[query: Q-LG-003]`.
- Execuções canônicas LangGraph: `0` `[query: Q-LG-004]`.
- Checkpoints, writes e blobs de aplicação: `0` em cada tabela `[query: Q-LG-009]`.
- Resoluções de modelo, canários reais, uso, chamadas de tools, datasets, bundles, change sets, hipóteses e memória de decisão: `0` linhas observadas em cada consulta aplicável `[query: Q-LG-013 a Q-LG-023]`.
- Ordens persistidas: `0` `[query: Q-LG-026]`.
- Perfis com `live_trading_enabled`: `0` de `53` `[query: Q-LG-025]`.
- Perfis com `auto_pilot_enabled`: `0` de `53` `[query: Q-LG-025]`.
- Pools com `autopilot_enabled`: `0` de `1` `[query: Q-LG-025]`.
- A reconciliação Spot permanece `NÃO PROVADA`; nenhuma precedência será inferida `[query: Q-LG-024]`.

Conclusão: infraestrutura e schema estão presentes, porém adoção real, provider efetivo, memória regenerativa e integração dos entrypoints continuam não provados em produção.

## Estado do staging isolado

Os serviços de API, worker LangGraph, PostgreSQL e Redis estão online `[comando: railway service list, ambiente systemic-ai-staging-20260807]`.

Flags observadas na API de staging `[config: Railway, valores não secretos]`:

```json
{
  "LANGGRAPH_RUNTIME_ENABLED": true,
  "LANGGRAPH_ENTRYPOINTS_ENABLED": true,
  "LANGGRAPH_REGENERATIVE_SHADOW_ENABLED": true,
  "LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED": false,
  "LANGGRAPH_STRICT_MSGPACK": true
}
```

Nenhuma credencial de provider foi observada como variável direta da API de staging `[config: Railway, presença booleana sem leitura de segredo]`. Isso não exclui chaves criptografadas no banco. A seleção de modelo e uma chamada real continuam bloqueadas até aprovação humana explícita de custo.

## Matriz prévia dos módulos vinculantes

| Módulo | Fonte operacional existente | Registro/tool sistêmico | Ação “Análise por IA” | Estado prévio |
|---|---|---|---|---|
| Strategy Profiles | `profiles`, Profile Intelligence e UI `/profiles` | parcial e genérico | ausente | PARCIAL |
| ML Models | `ml_model_registry` e UI `/ml-models` | parcial e genérico | ausente | PARCIAL |
| Shadow Portfolio | `shadow_trades`, relatórios e UI dedicada | ferramentas de leitura parciais | ausente | PARCIAL |
| Score Engine | `config_profiles` e UI `/settings/score` | leitura parcial | ausente | PARCIAL |
| Global Risk | `config_profiles` e UI `/settings/risk` | ausente | ausente | AUSENTE |
| Strategies | `config_profiles` e UI `/settings/strategies` | ausente | ausente | AUSENTE |
| Intelligence Runs | API `/api/ai/graphs` e UI `/intelligence-runs` | controle `v1` existente | tela existente | PARCIAL |
| Social Score | Social Intelligence e UI `/settings/social-score` | ausente | ausente | AUSENTE |
| Market Regime | serviços/leituras de regime | ferramenta genérica parcial | não exigida pela UI vinculante | PARCIAL |
| Audit/Version Memory | logs, eventos, change sets e decision memory | persistência genérica parcial | não exigida pela UI vinculante | PARCIAL |

O registro imutável dos dez módulos, seus schemas, dependências, tools autorizadas e hashes ainda não existe como contrato único. Os quatro entrypoints legados também não possuem ponte individual comprovada para request, resolução, dataset, bundle, resultado, uso e graph run canônicos.

## Decisão de implementação

Prosseguir de forma aditiva e fail-closed com:

- registro imutável versionado para os dez módulos;
- tools tipadas e tenant-scoped, todas read-only ou produtoras de candidato/shadow;
- contrato canônico multi-módulo com ausência explícita, nunca convertida em zero;
- grafos `v2` com fingerprint exato de contexto, veto determinístico de Risk/Strategies e bloqueio Spot;
- ponte canônica real para cada entrypoint legado;
- endpoint único para iniciar análise por módulo e UI rastreável em Intelligence Runs;
- migração Alembic aditiva, reversível e sem mutar registros históricos;
- flags de produção com default `false`;
- canários somente em staging até o checkpoint humano obrigatório.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| cabeça Alembic `148` | `[query: Q-LG-001]` | `148_langgraph_runtime` |
| testes focados `66` | `[comando: pytest focado]` | `66 passed` |
| backend `278` aprovados, `20` falhas | `[comando: pytest backend/tests --maxfail=20]` | `20 failed, 278 passed` |
| CatBoost `1.2.10` | `[comando: import catboost]` | `CATBOOST_VERSION=1.2.10` |
| frontend `23` de `23` | `[comando: npm test]` | `tests 23; pass 23; fail 0` |
| build `44` páginas | `[comando: npm run build]` | `Generating static pages ... (44/44)` |
| lint `371` erros | `[comando: npx eslint . --quiet]` | `371 problems (371 errors, 0 warnings)` |
| auditoria npm `4` altas, `0` críticas | `[comando: npm audit --omit=dev --json]` | `high: 4; critical: 0; total: 4` |
| timeout SQL `30s`; lock `3s` | `[query: Q-LG-000]` | `statement_timeout: 30s; lock_timeout: 3s` |
| execuções e checkpoints `0` | `[query: Q-LG-004, Q-LG-009]` | `data: []; row_count: 0` |
| live `0/53`; Auto-Pilot `0/53`; pool `0/1` | `[query: Q-LG-025]` | `live_trading_enabled 0 total 53; auto_pilot_enabled 0 total 53; autopilot_enabled 0 total 1` |
| ordens `0` | `[query: Q-LG-026]` | `row_count: 0` |
