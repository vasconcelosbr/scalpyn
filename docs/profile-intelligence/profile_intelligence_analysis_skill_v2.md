# profile_intelligence_analysis_skill_v2

## Objetivo

Produzir um diagnóstico executivo do Score Intelligence a partir de um payload
determinístico `pi-ai-analysis-v2`, sem recalcular métricas, misturar escopos ou
autorizar mutações fora do fluxo replay point-in-time → challenger shadow.

## Contratos obrigatórios

- `analysis_contract_version`: `pi-ai-analysis-v2`
- `analysis_skill_version`: `profile_intelligence_analysis_skill_v2`
- Fontes observacionais: `L1_SPECTRUM`, `L3`, `L3_LAB`, `L3_REJECTED`
- Chave canônica, por prioridade: `decision_id`, `event_id`, `ranking_id`
- Fallback heurístico por símbolo/tempo: proibido
- Escopos: `GLOBAL`, `COUNTERFACTUAL`, `PROFILE`
- `L3_REJECTED`: evidência `COUNTERFACTUAL`; nunca atribuída a um profile
- Recomendações: somente `candidate_id` com `validation.status=VALIDATED` e
  pertencente ao mesmo `profile_id`

## Bloqueios anteriores à IA

- `BLOCKED_CROSS_SOURCE_DEDUP_UNAVAILABLE`
- `BLOCKED_ANALYSIS_TRUNCATED`
- `TAUTOLOGICAL_OUTCOME_COHORT:*`
- `SOURCE_METRICS_NOT_EXHAUSTIVE:*`
- `INVALID_CONFUSION_MATRIX`
- `COUNTERFACTUAL_EVIDENCE_ATTRIBUTED_TO_PROFILE`
- `UNVALIDATED_CANDIDATE_EXPOSED`

Qualquer hard error encerra o run como `ANALYSIS_BLOCKED`. Nesse estado não há
chamada ao provider, relatório executivo nem envelope de ajustes.

## Método determinístico

1. Fixar `cutoff_at` e janela.
2. Carregar somente rows oficiais point-in-time.
3. Deduplicar pela chave canônica.
4. Calcular métricas completas por fonte e coorte.
5. Calcular a matriz de confusão:
   - prediction positive: `source in [L3,L3_LAB]`
   - actual positive: `outcome=TP_HIT`
   - timeout: classe negativa
6. Separar discovery/validation temporalmente em 70/30.
7. Construir candidatos somente com evidência profile-local.
8. Simular pontos `0,-1,-2,-3,-5,-7,-10` e
   `0,+1,+2,+3,+5,+7,+10`.
9. Calcular overlaps `A-only`, `B-only`, `AND` e `OR`.
10. Contabilizar separadamente candidate definitions, profile-rule
    applications e mutation instances.
11. Executar `validate_analysis_payload`.
12. Somente então enviar o payload à IA.
13. Executar `validate_ai_response_against_payload`.

## Restrições da resposta da IA

- JSON estruturado, sem texto fora do schema.
- Não inventar ou recalcular números.
- Não converter associação em causalidade.
- Não selecionar candidato inexistente, cross-profile ou não validado.
- Não recomendar treino, aprovação ou promoção de modelo.
- Não autorizar escrita em datasets L1/L3.
- Não mutar incumbent.
- Não ativar Auto-Pilot.
- Não aplicar ajuste diretamente.
- Todo número narrativo deve existir no payload determinístico; arredondamento
  de exibição é permitido, recálculo e novas metas são proibidos.
- Se o primeiro texto falhar no guard numérico, o mesmo modelo recebe uma única
  solicitação de revisão qualitativa sem algarismos. Não há fallback de modelo.

## Simulação obrigatória

- O score é lido do snapshot oficial.
- O score mínimo é resolvido em `signals.conditions`,
  `entry_triggers.conditions` ou `scoring.thresholds.buy`.
- O profile, profile version, score-engine version e hashes do row devem
  coincidir com o champion.
- São simulados os pontos `0,-1,-2,-3,-5,-7,-10` e
  `0,+1,+2,+3,+5,+7,+10`.
- Um candidate só existe quando há alternativa não zero que passa retenção,
  perda máxima de TP e redução mínima de SL.
- `-5` nunca é usado como default quando a simulação está ausente.

## Seleção de modelo

Allowlist:

- `claude-fable-5`
- `claude-opus-4-8`
- `claude-sonnet-5`
- `claude-haiku-4-5-20251001` (padrão)

A disponibilidade é consultada na Anthropic Models API com a chave configurada.
Não há fallback automático. O run persiste modelo solicitado e efetivo.

## Referências de implementação

- `backend/app/services/profile_intelligence_analysis_v2.py`
- `backend/app/services/profile_intelligence_ai_models.py`
- `backend/app/services/profile_score_optimization_service.py`
- `backend/app/api/profile_intelligence.py`
- `backend/app/models/profile_score_optimization.py`
- `backend/alembic/versions/139_profile_intelligence_ai_v2.py`
- `frontend/app/profile-intelligence/page.tsx`
- `frontend/app/profile-intelligence/ScoreIntelligencePanel.tsx`
