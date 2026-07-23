# Profile Intelligence — Skill de IA e seletor de modelos

Data: 2026-07-23  
Decisão: `IMPLEMENTED`  
Branch isolada: `codex/profile-intelligence-safe`  
Commit funcional final: `9ecc9e690cd51fd3eeba425e355a8b7283c8bef7`

## 1. Resultado executivo

O Profile Intelligence usa agora o contrato determinístico
`pi-ai-analysis-v2` e a Skill versionada
`profile_intelligence_analysis_skill_v2`. O backend calcula, reconcilia,
simula e valida; a IA interpreta e seleciona apenas candidatos fornecidos; o
humano continua responsável por replay, challenger e eventual aprovação.

O erro HTTP 504 foi eliminado movendo o processamento pesado para o worker
compute. O contexto enviado ao provider é limitado sem perder a superfície de
decisão, e a evidência integral continua persistida.

Nenhuma rotina desta entrega treina, aprova ou promove modelos, altera datasets
L1/L3, grava em `shadow_trades`, muta incumbent ou ativa Auto-Pilot.

## 2. Causas confirmadas

1. A análise síncrona excedia o timeout HTTP.
2. Evidência `L3_REJECTED` global era reapresentada como se fosse específica
   de cada profile.
3. O simulador não localizava o threshold real em `signals.conditions` e
   deixava a base de score indisponível.
4. Mesmo sem simulação, a proposta carregava `-5` como default.
5. Rows de versões históricas podiam ser comparadas contra o champion atual.
6. O primeiro schema estruturado continha `maxItems`, não aceito pelo endpoint
   Anthropic utilizado.
7. O payload inicial excedia a janela do Haiku.
8. A IA arredondava métricas e criava metas/derivações não existentes no
   payload.
9. Dois uploads Railway usaram raiz incompatível com
   `rootDirectory=backend`; os deployments falharam antes de substituir os
   serviços saudáveis.

## 3. Skill e contrato v2

- Analysis contract: `pi-ai-analysis-v2`
- Skill: `profile_intelligence_analysis_skill_v2`
- Regra: `Deterministic backend computes. AI reviews and explains. Human approves.`
- Dedup: `decision_id → event_id → ranking_id`
- Fallback heurístico por símbolo/tempo: proibido
- Escopos separados: `GLOBAL`, `COUNTERFACTUAL`, `PROFILE`
- `L3_REJECTED`: somente `COUNTERFACTUAL`
- Candidate profile-local: exige profile/version/score-engine/hashes iguais ao
  champion analisado

## 4. Checks determinísticos

O `validate_analysis_payload()` bloqueia:

- contrato ou Skill incorretos;
- chave canônica ausente;
- truncamento;
- coorte tautológica;
- contagens, taxas ou P&L incompatíveis;
- candidate sem escopo e versões completas;
- candidate duplicado;
- simulação incompleta;
- seleção não derivada de simulação;
- overlap incompleto;
- evidência counterfactual atribuída ao profile;
- candidate não validado.

Estados de bloqueio relevantes:

- `BLOCKED_CROSS_SOURCE_DEDUP_UNAVAILABLE`
- `BLOCKED_ANALYSIS_TRUNCATED`
- `TAUTOLOGICAL_OUTCOME_COHORT:*`
- `BLOCKED_SCOPE_MISMATCH:*`
- `BLOCKED_IMPACT_NOT_SIMULATED:*`
- `BLOCKED_RULE_OVERLAP_NOT_SIMULATED`
- `AI_RESPONSE_REJECTED_NUMERIC_OR_SCOPE_MISMATCH`
- `ANALYSIS_BLOCKED_MODEL_UNAVAILABLE`

## 5. Deduplicação e dataset do smoke final

Run: `059ceea7-c159-408e-ad75-30295274108e`  
Cutoff: `2026-07-23T18:05:01.241649Z`  
Janela: 30 dias, limitada por
`NATIVE_CAPTURE_START_AT=2026-07-12T18:21:57Z`.

| Fonte | Closed | TP | SL | TIMEOUT |
|---|---:|---:|---:|---:|
| L1_SPECTRUM | 2.349 | 1.181 | 1.161 | 7 |
| L3 | 4.298 | 2.089 | 2.152 | 57 |
| L3_LAB | 5.303 | 2.847 | 2.332 | 124 |
| L3_REJECTED | 68.859 | 33.245 | 35.375 | 239 |
| Total | 80.809 | 39.362 | 41.020 | 427 |

- raw rows: 80.809
- unique opportunities: 80.809
- duplicates removidos: 0
- chave canônica ausente: 0
- truncado: não
- validação pré-IA: válida, sem warning ou hard error

## 6. Escopo e matriz de decisão

Approved consolidado (`L3 + L3_LAB`):

- closed: 9.601
- TP: 4.936
- SL: 4.484
- TIMEOUT: 181
- TP rate: `4.936 / 9.601`
- SL rate: `4.484 / 9.601`

Counterfactual `L3_REJECTED`:

- closed: 68.859
- TP: 33.245
- SL: 35.375
- TIMEOUT: 239
- não pode ser atribuído a profile.

Matriz:

| Classe | N |
|---|---:|
| TP — aprovado e TP_HIT | 4.936 |
| FP — aprovado e não TP_HIT | 4.665 |
| FN — rejeitado e TP_HIT | 33.245 |
| TN — rejeitado e não TP_HIT | 35.614 |

TIMEOUT é classe negativa separada conforme o contrato; rejected SL nunca é
chamado de falso negativo.

## 7. Simulações, overlap e quantidade de ajustes

Alternativas:

- penalties: `0, -1, -2, -3, -5, -7, -10`
- bônus: `0, +1, +2, +3, +5, +7, +10`

Cada alternativa contém trades aprovados/rejeitados, TP preservados/perdidos,
SL evitados/preservados, TIMEOUT, volume, P&L e score mínimo.

O backend escolhe pontos somente entre alternativas simuladas que passam:

- retenção mínima;
- perda máxima de TP;
- redução mínima de SL.

Resultado do run final:

- candidate definitions: 14
- profile-rule applications: 30
- alternativas simuladas: 390
- mutation instances: 360
- distribuição determinística: `-2: 5`, `-3: 10`, `-5: 7`, `-7: 2`,
  `-10: 6`
- overlaps: `A-only`, `B-only`, `AND`, `OR` para todos os pares aplicáveis

## 8. Guard pós-resposta

O `validate_ai_response_against_payload()` exige:

- contrato e Skill corretos;
- candidate existente, validado e do mesmo profile;
- máximo de três IDs por profile;
- nenhuma seleção cross-profile;
- números narrativos presentes no payload, admitindo apenas arredondamento de
  exibição.

Se a primeira narrativa contiver número inventado ou derivado, há uma única
revisão pelo mesmo modelo. A revisão remove algarismos, percentuais, datas,
metas e thresholds dos campos textuais; IDs estruturais são preservados. Não há
troca de modelo. Se a revisão também falhar, o run termina bloqueado.

No smoke final ocorreram duas chamadas ao mesmo Haiku, a segunda passou:

- narrative numeric tokens não verificados: 0
- recomendações por profile: 15
- candidates selecionados: 23 únicos
- IDs desconhecidos ou cross-profile: 0

## 9. Seletor de modelos

Disponível em `Profile Intelligence > Settings`:

- atualizar disponibilidade;
- testar modelo;
- salvar modelo verificado;
- exibir provider, descrição, status, capacidade e última verificação;
- impedir salvamento quando indisponível;
- avisar que o modelo só entra no próximo run.

Allowlist:

- `claude-fable-5`
- `claude-opus-4-8`
- `claude-sonnet-5`
- `claude-haiku-4-5-20251001` (default)

Configuração desconhecida ou modelo indisponível bloqueia a análise. Não há
fallback silencioso.

## 10. Disponibilidade e capabilities

Com a chave de produção, os quatro IDs retornaram `AVAILABLE`.

Request de listagem: `req_011CdKKn338tpDQdmAuM5KYc`  
Request de refresh pelo endpoint: `req_011CdKMdwnPBe8Wt6rGGfzho`  
Request de teste Haiku: `req_011CdKMe1Gyk48Ep9cUd77aG`

| Modelo | Input | Output | Structured output |
|---|---:|---:|---|
| Fable 5 | 1.000.000 | 128.000 | sim |
| Opus 4.8 | 1.000.000 | 128.000 | sim |
| Sonnet 5 | 1.000.000 | 128.000 | sim |
| Haiku 4.5 | 200.000 | 64.000 | sim |

Parâmetros incompatíveis são omitidos. Indisponibilidade, autenticação,
rate-limit ou timeout são sanitizados em estados públicos sem expor a chave.

## 11. Persistência e auditoria

O config persiste provider, modelo, status, verificação, capabilities e versão
da Skill. Cada run persiste modelo solicitado/efetivo e contrato/Skill.

A migration `139_pi_ai_v2` é aditiva:

- quatro colunas nullable no run;
- tabela `profile_intelligence_ai_model_audit`;
- nenhum DML;
- nenhuma alteração em ML, `shadow_trades`, profiles, score-engine versions ou
  registry.

## 12. Testes

- suite Profile Intelligence/Score Intelligence: 139 passed
- frontend `next build`: aprovado, 42 páginas
- Alembic: uma head, `139_pi_ai_v2`
- schema gate: 34/34
- teste direto Anthropic structured output:
  `req_011CdKMpM3ePpWJHEFe2cJhw`
- teste de reprodução do guard:
  `req_011CdKRdGFaDTL8Qo1Aw9utq`

Regressões cobertas: tautologia, dedup, chave ausente, confusion matrix,
cross-profile, contexto limitado, schema Anthropic, simulação sem default,
threshold em Signals, modelo desconhecido, número inventado, arredondamento
permitido e isolamento ML.

## 13. Isolamento ML

Arquivo dedicado:
`backend/tests/test_profile_intelligence_ai_skill_ml_isolation.py`.

Evidência pós-smoke:

- training runs criados: 0
- model candidates/promotions: 0
- dataset/feature/label mutations: 0
- writes em `shadow_trades`: 0
- replay rows: 0
- challenger rows: 0
- incumbent mutated: false
- eligible_for_training: false
- training_or_promotion_allowed: false

## 14. Deploy

Produção:

- API Railway: `eb2e7ba1-e829-49ef-acf4-42fe08b649f5` — `SUCCESS`
- worker compute Railway:
  `48cbe99e-a8e8-4648-9c0b-03e9a6c5f110` — `SUCCESS`
- frontend Vercel: `dpl_DUyP41ggnXFmadLAAaMnXzoAEgCL` — `READY`
- rota: `https://scalpyn.vercel.app/profile-intelligence`

Readiness efetiva:

- schema 34/34;
- Redis connected;
- task `profile_score_optimization.analyze` registrada;
- Celery ready;
- application startup complete;
- `/api/health`: 200;
- `/api/health/schema`: 200;
- `/profile-intelligence`: 200.

## 15. Smoke final

- run: `059ceea7-c159-408e-ad75-30295274108e`
- task: `2a252162-2999-4dbd-9f8b-1077f314f607`
- status: `AI_COMPLETED`
- duração do worker: 139,818 s
- provider/modelo solicitado/efetivo:
  `anthropic / claude-haiku-4-5-20251001`
- pre-AI validation: válida
- response contract/Skill: válidos
- envelope: 23 mudanças, exatamente os 23 IDs selecionados
- safety: shadow-only
- replay/challenger: 0/0

## 16. Ledger operacional

| Estado | Evidência | Classificação |
|---|---|---|
| Schema com `maxItems` rejeitado pelo provider | HTTP 400 | `INVALIDO_HISTORICO` |
| Contexto inicial acima da janela Haiku | 255.721 tokens | `INVALIDO_HISTORICO` |
| Run com `-5` default e simulação indisponível | `9941baf8-...` | `INVALIDO_HISTORICO` |
| Upload com root duplicado | `ddaf6adb-...`, `b240bf62-...` FAILED | corrigido |
| Primeira resposta sob guard numérico | `290a3422-...` AI_FAILED | `INVALIDO_HISTORICO` |
| Run final pós-readiness | `059ceea7-...` AI_COMPLETED | `VALIDADO_POS_DEPLOY` |

## 17. Exemplo de relatório corrigido

O relatório textual final é qualitativo e não contém números criados pela IA.
Os valores exibidos ao usuário vêm do payload determinístico e do envelope.
Cada recomendação referencia apenas candidate IDs já simulados e validados no
mesmo profile/version/hash.

## 18. Riscos residuais

- O relatório pode exigir uma segunda chamada ao mesmo modelo quando a primeira
  narrativa viola o guard, aumentando latência e custo.
- A análise é observacional; replay point-in-time e challenger shadow continuam
  obrigatórios antes de qualquer decisão humana de aplicar.
- Amostras continuam crescendo e os resultados podem mudar em runs futuros.
- Os erros HTTP 451 da Binance observados em tarefas paralelas do mesmo worker
  não pertencem ao fluxo de Profile Intelligence e não afetaram o run final.

## 19. Arquivos principais

- `docs/profile-intelligence/profile_intelligence_analysis_skill_v2.md`
- `backend/app/services/profile_intelligence_analysis_v2.py`
- `backend/app/services/profile_intelligence_ai_models.py`
- `backend/app/services/profile_score_optimization_service.py`
- `backend/app/api/profile_intelligence.py`
- `backend/app/models/profile_score_optimization.py`
- `backend/alembic/versions/139_profile_intelligence_ai_v2.py`
- `backend/tests/test_profile_intelligence_ai_skill_ml_isolation.py`
- `frontend/app/profile-intelligence/page.tsx`
- `frontend/app/profile-intelligence/ScoreIntelligencePanel.tsx`

## 20. Critérios de aceite

Todos os critérios do prompt foram implementados e validados. Nenhum ajuste foi
aplicado em produção durante o deploy.
