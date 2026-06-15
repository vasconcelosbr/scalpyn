# L2 Validação Direcional (v2)

Experimento para responder: **existe edge direcional realizável e líquido de custo na base L1_SPECTRUM?**

## Invariantes

1. **Read-only em produção** — nenhuma alteração em `config_profiles`, `shadow_trades`, `ml_models` ativos.
2. **Zero hardcode** — todo threshold lido de `config_profiles config_type='ml_research'`.
3. **Additive-only** — nenhum arquivo/rota/tabela existente é removido.
4. **Split temporal sempre** — proibido split aleatório.
5. **Sem leakage** — threshold operacional ajustado APENAS no validation set.

## Execução das Fases

```bash
# Fase 0 — Pré-registro + verificação de poder estatístico
railway run python -m research.l2_directional.phase_00_setup

# Fase 1 — Inventário de features (stubs, nulos, variância)
railway run python -m research.l2_directional.phase_01_inventory

# Fase 2 — Construção de labels (retorno forward 30m/60m líquido de fee)
railway run python -m research.l2_directional.phase_02_labels
```

## Ordem obrigatória

```
Fase 0 → (gate de poder) → Fase 1 → (gate de fluxo) → Fase 2 → Fases 3-9
```

- Se o gate da Fase 0 falhar (Top 10% < `ml.min_bucket_n`): **PARAR** e aguardar acúmulo de dados.
- Se features de fluxo forem stubs (Fase 1): **PARAR** e corrigir `feature_engine` antes de treinar.

## Pré-registro

Antes de rodar qualquer treino (Fase 4), confirmar valores com Ricardo e travar:

```sql
UPDATE config_profiles
SET config_json = config_json || '{
    "ml.preregistration_locked": true,
    "ml.preregistration_date": "YYYY-MM-DD",
    "ml.cost_roundtrip_pct": 0.0040,
    "ml.slippage_pct": 0.0005
}'::jsonb
WHERE config_type = 'ml_research' AND is_active = true;
```

## Tabelas de experimento (additive)

| Tabela | Criada por | Conteúdo |
|---|---|---|
| `ml_experiment_labels` | Fase 2 | Labels forward return + MFE/MAE por shadow_trade |

## Critérios GO (pré-registrados)

| GO | Critério |
|---|---|
| Direcional | IC Spearman rank > 0.03 E p < 0.05 no test set |
| Operacional | EV líquido Top 10% > 0 E IC 95% inferior > EV base |
| Realizabilidade | Edge sobrevive simulação TP/SL real (Fase 5.4) |

**Veredito**: os 3 GO devem passar simultaneamente.
