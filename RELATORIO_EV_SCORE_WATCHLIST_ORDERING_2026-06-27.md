# Relatório — EV Score e ordenação unificada de Watchlists

Data: 2026-06-27  
Projeto: Scalpyn  
Veredito: `WATCHLIST_PERFORMANCE_ORDERING_OPERATIONAL_WITH_TUNING_PENDING`

## 1. Resumo executivo

Foi implantado um ranking único e dinâmico de performance para as watchlists L3. Shadow Portfolio, `/watchlist` e API L3 consomem o mesmo serviço, com ordenação padrão por EV Score decrescente e desempates por confiança, P&L médio, amostra, TP4h e P&L total. [código: `backend/app/services/watchlist_performance_ranking_service.py:298`]

Após a primeira implantação, a validação literal detectou históricos órfãos no universo do Shadow/L3 e divergência com as watchlists atuais. A correção restringiu o ranking ao par atual `watchlist_id + profile_id` de L3. O redeploy final retornou 102 registros em cada consumidor e Top 10 idêntico em ID, profile, EV Score e prioridade. [curl response]

O maior EV Score observado é 48,39, prioridade B. Como os pesos ainda não produziram A/A+ nos dados atuais, o modelo está operacional, mas permanece sujeito a tuning após observação. [curl response]

## 2. Fórmula e pesos

```text
ev_score =
  clamp(avg_pnl_pct / 1.0 * 35, -20, 35)
  + clamp(win_rate * 20, 0, 20)
  + clamp(ln(max(completed_trades, 1)) / ln(500) * 15, 0, 15)
  + clamp(tp_4h_rate * 15, 0, 15)
  + clamp(pnl_total_usdt / 1000 * 10, -10, 10)
  - holding_penalty
  - low_n_penalty
  - negative_pnl_penalty
```

A unidade canônica de `avg_pnl_pct` no banco é `1.0 = 1%`. O score final é limitado a 0–100. [config/código]

Pesos: P&L médio 35; win rate 20; amostra 15; TP4h 15; P&L total 10. Penalidades: holding >4h = 5; >8h = 10; N<30 = 30; N<50 = 15; N<100 = 5; P&L médio negativo = 25; P&L total negativo = 10. [config SQL]

Configuração integral persistida:

```json
{"limits":{"score_max":100,"score_min":0,"pnl_component_min":-20},"version":1,"weights":{"pnl":35,"tp4h":15,"sample":15,"win_rate":20,"pnl_total":10},"penalties":{"low_n_under_30":30,"low_n_under_50":15,"holding_over_4h":5,"holding_over_8h":10,"low_n_under_100":5,"negative_avg_pnl":25,"negative_total_pnl":10},"thresholds":{"priority_a":60,"priority_b":45,"priority_c":30,"sample_low":50,"sample_high":300,"sample_low_n":30,"tp4h_seconds":14400,"good_win_rate":0.50,"sample_medium":100,"good_tp4h_rate":0.40,"low_n_score_cap":44.99,"priority_a_plus":75,"shadow_tp4h_rate":0.20,"holding_severe_seconds":28800,"holding_warning_seconds":14400},"normalization":{"sample_target":500,"avg_pnl_pct_target":1.0,"pnl_total_usdt_target":1000},"source_filter":["L3","L3_LAB"]}
```

## 3. Persistência, queries e atualização dinâmica

A migration `114_watchlist_priority` cria `watchlist_performance_priority_base_view`, agregando shadow trades por usuário, profile, watchlist e origem. A view possui 488 linhas-base em produção. [SQL]

O serviço consulta a view e cruza com `pipeline_watchlists` pelo par atual de watchlist/profile L3. Não há cache nem job: cada leitura usa o estado mais recente de `shadow_trades`. [código: `backend/app/services/watchlist_performance_ranking_service.py:318`]

A configuração é persistida em `config_profiles` com tipo `watchlist_performance_ranking`; existe 1 configuração ativa em produção. O runtime falha fechado se ela estiver ausente ou inválida. [SQL/código]

A migration aplicada é `114_watchlist_priority`. [SQL]

## 4. Endpoints ajustados

- `GET /api/shadow-trades/profile-report?order_by=ev_score&direction=desc`
- `GET /api/shadow-portfolio/report?order_by=ev_score&direction=desc`
- `GET /api/watchlists/?order_by=performance_priority` — nome real equivalente ao endpoint sugerido `/pipeline`
- `GET /api/l3/watchlists?order_by=performance_priority`
- `GET /api/l3/candidates?order_by=performance_priority`
- `GET /api/l3/profiles?order_by=performance_priority`

As rotas Shadow, Watchlist e L3 principais responderam HTTP 200 em 1,069 s, 0,447 s e 0,419 s, respectivamente. [curl response]

## 5. Shadow Portfolio

Foram preservadas as colunas existentes e adicionadas `Prioridade`, `EV Score`, `Confiança`, `Delta vs Baseline` e `Motivo`. O tooltip documenta componentes e penalidades. A tabela abre por EV Score DESC e permite override manual. [código: `frontend/app/dashboard/shadow-portfolio/page.tsx:2114`]

## 6. Watchlist

Os grupos POOL, L1, L2 e L3 permanecem. Dentro de L3, o backend ordena por `performance_priority_order`, originado diretamente de `rank_position`. Os cards L3 exibem prioridade, EV Score, confiança, P&L médio, win rate, amostra e motivo. [código: `backend/app/api/watchlists.py:847`; `frontend/app/watchlist/page.tsx:1110`]

## 7. APIs L3

Watchlists, candidates e profiles usam o mesmo `get_performance_rankings(..., level="L3")`. Nenhuma fórmula foi duplicada nos endpoints. [código: `backend/app/api/performance_rankings.py:41`]

## 8. Top 10 literal em produção

| rank | profile / watchlist | EV Score | prioridade | trades | win rate | TP4h | P&L médio | P&L total | motivo |
|---:|---|---:|---|---:|---:|---:|---:|---:|---|
| 1 | adx_gte_35_AND_bb_0_050_0_080 / `090cf6a1` | 48,39 | B | 69 | 57,97% | 85,00% | +0,449275% | 310,00 | P&L médio positivo; 69 trades; GOOD_4H |
| 2 | bb_0_050_0_080_AND_ema50_gt_ema200_false / `78afad64` | 43,12 | C | 165 | 50,30% | 83,13% | +0,257576% | 425,00 | P&L médio positivo; 165 trades; GOOD_4H |
| 3 | macd_hist_lte_0_AND_bb_0_050_0_080 / `5a5b4b31` | 39,00 | C | 98 | 53,06% | 84,62% | +0,326531% | 320,00 | P&L médio positivo; 98 trades; GOOD_4H |
| 4 | rsi_gte_72_AND_bb_0_050_0_080 / `9b901277` | 38,81 | LOW_N | 10 | 80,00% | 75,00% | +1,000000% | 100,00 | Rebaixado: apenas 10 trades |
| 5 | vol_spike_gte_1_5_AND_bb_0_050_0_080 / `4a93226f` | 35,01 | C | 74 | 47,30% | 82,86% | +0,182432% | 135,00 | P&L médio positivo; GOOD_SHADOW_BAD_4H |
| 6 | adx_gte_35_AND_ema50_gt_ema200_false / `76927049` | 32,94 | C | 148 | 44,59% | 74,24% | +0,116943% | 173,0760 | P&L médio positivo; GOOD_SHADOW_BAD_4H |
| 7 | macd_hist_lte_0_AND_adx_gte_35_AND_ema50_gt_ema200_false / `ca349282` | 25,77 | D | 112 | 41,07% | 67,39% | +0,023011% | 25,7722 | Amostra média; score abaixo de C |
| 8 | macd_hist_lte_0_AND_ema50_gt_ema200_false / `152889c2` | 23,92 | D | 242 | 41,32% | 70,00% | +0,032249% | 78,0423 | Amostra média; score abaixo de C |
| 9 | rsi_gte_72_AND_adx_gte_35_AND_bb_0_050_0_080 / `62e9d2bd` | 21,03 | LOW_N | 7 | 71,43% | 60,00% | +0,785714% | 55,00 | Rebaixado: apenas 7 trades |
| 10 | vwap_2_0_3_0_AND_macd_hist_lte_0 / `5d092b07` | 19,25 | D | 37 | 45,95% | 70,59% | +0,148649% | 55,00 | Confiança LOW; score abaixo de C |

Origem de todos os valores da tabela: respostas autenticadas de produção dos três endpoints, coletadas após o deployment final. [curl response]

## 9. Consistência entre consumidores

| posição | Shadow Portfolio | Watchlist | API L3 | Status |
|---:|---|---|---|---|
| 1 | `090cf6a1` / 48,39 / B | `090cf6a1` / 48,39 / B | `090cf6a1` / 48,39 / B | PASS |
| 2 | `78afad64` / 43,12 / C | `78afad64` / 43,12 / C | `78afad64` / 43,12 / C | PASS |
| 3 | `5a5b4b31` / 39,00 / C | `5a5b4b31` / 39,00 / C | `5a5b4b31` / 39,00 / C | PASS |
| 4 | `9b901277` / 38,81 / LOW_N | `9b901277` / 38,81 / LOW_N | `9b901277` / 38,81 / LOW_N | PASS |
| 5 | `4a93226f` / 35,01 / C | `4a93226f` / 35,01 / C | `4a93226f` / 35,01 / C | PASS |
| 6 | `76927049` / 32,94 / C | `76927049` / 32,94 / C | `76927049` / 32,94 / C | PASS |
| 7 | `ca349282` / 25,77 / D | `ca349282` / 25,77 / D | `ca349282` / 25,77 / D | PASS |
| 8 | `152889c2` / 23,92 / D | `152889c2` / 23,92 / D | `152889c2` / 23,92 / D | PASS |
| 9 | `62e9d2bd` / 21,03 / LOW_N | `62e9d2bd` / 21,03 / LOW_N | `62e9d2bd` / 21,03 / LOW_N | PASS |
| 10 | `5d092b07` / 19,25 / D | `5d092b07` / 19,25 / D | `5d092b07` / 19,25 / D | PASS |

A comparação programática do Top 10 retornou `true` para Shadow×Watchlist, Shadow×L3 e Watchlist×L3. [curl response]

## 10. LOW_N e BLOCKED

Existem 11 linhas LOW_N com 1–29 trades entre as 102 watchlists L3 atuais; o maior score LOW_N é 38,81. Não houve violação do guardrail em relação a A/A+ e não houve linha com zero trades fora de `BLOCKED`. [curl response]

## 11. Testes e verificações

- Teste focado inicial: 10 passed. [test output]
- Teste focado após correção de consistência: 11 passed em 1,16 s. [test output]
- Frontend unitário: 17/17 passed. [test output]
- TypeScript `tsc --noEmit`: PASS. [test output]
- Next.js production build: PASS; 40 páginas. [build output]
- `py_compile` dos arquivos alterados: PASS. [test output]
- Alembic heads: `114_watchlist_priority (head)`. [test output]
- SQL offline `113:114`: PASS. [test output]
- `git diff --check`: PASS, somente aviso de normalização LF/CRLF. [git output]
- Suíte backend ampliada: 147 passed, 914 deselected, 3 failed, 12 errors; falhas externas ao escopo incluem serviços esperados em localhost:8001, baseline preexistente de monitoramento e isolamento de pipeline. [test output]
- Docker local: não executado porque o daemon Docker Desktop não estava disponível. [tool output]

## 12. Deploy

| componente | commit | deployment | status | timestamp |
|---|---|---|---|---|
| Backend — migration/feature | `9b83e36b14831ec979f2c7a13348a82815f7a193` | `974b5ae0-9de3-44f4-ae28-357d9a01a38d` | SUCCESS | 2026-06-27T17:06:15.260Z |
| Backend — correção final | `4ae94aa578be1deda227fe4ab359ee9aed05db71` | `50aca375-f02a-4445-a3e1-c3c0eaf3f94c` | SUCCESS | 2026-06-27T17:30:27.498Z |
| Frontend | `9b83e36` | `dpl_sMMjJXvqtnPkkKnihCK8raoaZmNG` | READY / Production | timestamp não retornado pela coleta |

Frontend de produção: `https://frontend-ecru-eight-91.vercel.app`. A correção final alterou apenas backend e teste. [deployment output/git diff]

## 13. Screenshots

`NÃO DISPONÍVEL` — os runtimes Chrome e Computer Use falharam antes de abrir a aplicação com erro de inicialização `missing field sandboxPolicy`. Não foi fabricada evidência visual. A UI foi validada por typecheck, testes e build, e o contrato de dados foi validado diretamente em produção. [tool error/test output/API]

## 14. Safety final

Auditoria executada em transação `READ ONLY` e encerrada com `ROLLBACK`:

| controle | valor literal | status |
|---|---:|---|
| live_enabled | 0 | PASS |
| possible_live_orders | 0 | PASS |
| active_new_models | 0 | PASS |
| profiles_created_24h | 0 | PASS |
| production_mutations_24h | 0 | PASS |
| ML_GATE_ENABLED | false | PASS |
| total_profiles | 109 | informativo |
| autopilot_enabled | 1 | informativo; estado preexistente e fora dos critérios de bloqueio |

Nenhum profile ou watchlist foi criado/apagado por esta entrega; a única persistência funcional nova é a configuração do ranking e a view via migration. [git diff/SQL]

## 15. Checklist contratual

| Contrato | Status | Evidência |
|---|---|---|
| EV Score calculado | PASS | API/código |
| Confiança Estatística calculada | PASS | API/código |
| Delta vs Baseline calculado | PASS | API/código |
| Prioridade calculada | PASS | API/código |
| Motivo da Prioridade presente | PASS | API/UI |
| LOW_N penalizado | PASS | 11 linhas; máximo 38,81; zero violações |
| Shadow Portfolio ordenado | PASS | API + frontend build; screenshot indisponível |
| Watchlist L3 mesma ordem | PASS | comparação Top 10 |
| APIs L3 mesma ordem | PASS | comparação Top 10 |
| Atualização dinâmica | PASS | view + cálculo por request |
| Safety final | PASS | SQL read-only |

## 16. Ledger de evidências

| Afirmação | Origem | Valor literal |
|---|---|---|
| Commit final de código | `git rev-parse HEAD` | `4ae94aa578be1deda227fe4ab359ee9aed05db71` |
| Deployment final backend | Railway deployment list | `50aca375-f02a-4445-a3e1-c3c0eaf3f94c`, SUCCESS |
| Migration aplicada | SQL `alembic_version` | `114_watchlist_priority` |
| View presente | SQL `to_regclass` | `watchlist_performance_priority_base_view` |
| Linhas-base da view | SQL | 488 |
| Configuração ativa | SQL | 1 |
| Universo por consumidor | curl | 102 / 102 / 102 |
| HTTP e latência | curl | 200: 1,069 s / 0,447 s / 0,419 s |
| Consistência Top 10 | comparação curl | true / true / true |
| Maior EV Score | curl | 48,39 |
| LOW_N | curl | 11; máximo 38,81; 0 violações |
| BLOCKED inválido | curl | 0 |
| Teste focado final | pytest | 11 passed em 1,16 s |
| Frontend unitário | npm test | 17/17 passed |
| Build frontend | npm run build | PASS; 40 páginas |
| Safety final | SQL/env | 0/0/0/0/0; ML Gate false |
| Evidência visual | runtimes de browser | NÃO DISPONÍVEL — `missing field sandboxPolicy` |

## 17. Correção complementar — dashboard frontend

Após validação do usuário, foi identificada uma lacuna na entrega visual: as métricas estavam nas tabelas existentes, mas não havia um dashboard frontend dedicado. A correção adicionou:

- Rota: `/dashboard/watchlist-performance`.
- Menu Back Office: `Ranking de Watchlists`.
- KPIs de universo L3, maior EV Score, amostra confiável, LOW_N e P&L positivo.
- Distribuição por prioridade, Top 10 visual e tabela operacional completa.
- Busca, filtro por prioridade, refresh manual e atualização automática a cada 30 segundos.
- Links diretos para Shadow Portfolio e Watchlist.

Evidências da correção:

| Afirmação | Origem | Valor literal |
|---|---|---|
| Commit do dashboard | git | `54abaae` |
| Testes frontend | npm test | 19/19 passed |
| Build frontend | Next.js/Vercel | 41 páginas; rota `/dashboard/watchlist-performance` |
| Deployment frontend final | Vercel | `dpl_BEnkqwL8mH5XzJ1aBQDiGRNSBszg`, READY |
| URL pública | HTTP | `/dashboard/watchlist-performance` = 200 |
| Proxy do dashboard | HTTP autenticado | 200; 102 linhas |
| Maior score na validação final | proxy frontend | 48,32; prioridade B |
| Configuração corrigida | Vercel env | `BACKEND_URL` definido para Production, Preview e Development |

O primeiro deploy da rota retornava a página, mas o proxy ainda respondia 502 porque `BACKEND_URL` não existia no projeto Vercel e o fallback era `localhost:8000`. A variável foi configurada para o backend Railway público e um novo deploy de produção foi concluído.

## 18. Veredito

`WATCHLIST_PERFORMANCE_ORDERING_OPERATIONAL_WITH_TUNING_PENDING`

A ordenação única está operacional e consistente nos três consumidores, com guardrails de amostra e safety aprovados. O tuning permanece pendente porque, na fotografia atual, o maior score é 48,39 e nenhuma watchlist atingiu A/A+.