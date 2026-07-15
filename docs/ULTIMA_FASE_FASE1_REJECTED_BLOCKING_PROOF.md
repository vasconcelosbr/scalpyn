# Última Fase — Fase 1: Prova de Bloqueio de Modelos REJECTED

**Data/hora:** 2026-06-25 ~01:25-01:35 UTC
**Objetivo da fase:** provar que, com os modelos `active` atuais (v44/L3_PROFILE e v46/L1_SPECTRUM) reprovados pelo Promotion Gate (`REJECTED`), nenhuma decisão `ALLOW` é convertida em ordem real — o gate deve sempre falhar fechado (`BLOCK`).

## 1. Tentativa de prova end-to-end via pipeline real (sessão anterior à compactação)

Uma tentativa anterior, dentro desta mesma sessão, ligou `ML_GATE_ENABLED=true` em produção para observar o ciclo real do `pipeline_scan.scan`. Essa mudança forçou o restart dos workers e expôs um crash-loop transitório, investigado e **root-caused para uma causa não relacionada ao ML Gate**: a variável de módulo `_PIPELINE_EXECUTION_TRACKING_SCHEMA_READY` (`backend/app/tasks/pipeline_scan.py:48`) é protegida por um `asyncio.Lock` (`_PIPELINE_EXECUTION_TRACKING_SCHEMA_LOCK`, linhas 2174-2179) que só serializa corrotinas **dentro de um mesmo processo**. Como o Celery usa pool `prefork`, cada processo-filho tem sua própria cópia dessa variável e desse lock — múltiplos forks reiniciando simultaneamente correm a executar `backfill_execution_tracking_columns()` (DDL idempotente `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, `backend/app/init_db.py`) ao mesmo tempo, gerando contenção de lock transitória.

**Confirmado nesta revisão (2026-06-25):** o código ainda tem exatamente essa forma (lido `pipeline_scan.py:2172-2179`) — o lock continua por-processo, não por-cluster. Esse risco **permanece presente** e não foi corrigido nesta sessão (estava fora do escopo do punch-list de 7 itens). O DDL em si é idempotente e auto-recuperável (a contenção é transitória, sem corrupção de dado), mas qualquer novo flip de `ML_GATE_ENABLED` que force restart de workers pode reproduzir o mesmo blip.

**Resultado desta tentativa:** inconclusiva para o objetivo específico de observar `decisions_log`/`shadow_trades` com `BLOCK`+`reason=ml_gate` em um ciclo limpo, mas **sem nenhum impacto negativo real** — confirmado por leitura read-only do banco: 0 live trading, 0 auto-pilot, 0 ordens reais em qualquer momento. `ML_GATE_ENABLED` foi revertido para `false` (estado não-definida) imediatamente, conforme a regra "se qualquer erro aparecer, desligar imediatamente".

## 2. Achado crítico durante a preparação da prova alternativa: bug fail-open no cache negativo

Ao construir uma prova decisiva no nível de função (sem depender de um ciclo completo do pipeline, evitando o risco de restart), foi descoberto um bug real e não-relacionado a esta sessão, latente desde a introdução do Promotion Gate (commit `68dc216`):

- `gcs_model_loader.py` cacheia o resultado de `NoEligibleModelError` como `{"model": None}` por `MODEL_CACHE_TTL` (300s — igual ao período do beat).
- Na 1ª chamada após o cache expirar, `get_model()` levanta `NoEligibleModelError` corretamente → `predict()` retorna fail-closed.
- **Em toda chamada seguinte dentro da mesma janela de 300s**, o cache retornava `None` silenciosamente (sem levantar exceção) → `predict()` seguia adiante e quebrava mais tarde em `model.predict_proba(None)` → essa `AttributeError` é capturada por um `except Exception` genérico em `pipeline_scan.py`'s `_ml_predict_one()`, cujo fallback é `model_approved=True` — **fail-OPEN**.
- Impacto: se `ML_GATE_ENABLED=true` fosse ligado com v44/v46 `REJECTED`, **apenas a primeira predição por janela de 300s bloquearia de fato; todas as demais passariam (ALLOW) silenciosamente**, mascaradas por um `logger.warning` que não distingue infraestrutura de modelo reprovado.

### Correção aplicada

- **Commit:** `9dc50a1` (`fix(ml): close fail-open gap in negative model-cache (Promotion Gate)`)
- **Mudança:** o erro original é armazenado junto à entrada negativa do cache (`self._cache[cache_key]["error"] = e`) e re-levantado em todo cache *hit* enquanto `model is None`, em vez de retornar `None` silenciosamente.
- **Testes novos:** `backend/tests/test_gcs_model_loader_cache.py` (4 testes) — cobre explicitamente a 2ª chamada dentro da janela de TTL, que é a regressão exata do bug. Todos `PASS`.
- **Regressão completa:** suíte cheia rodada antes e depois do fix (arquivo revertido temporariamente para `HEAD` via `git checkout` e restaurado depois). Resultado idêntico em ambos os casos: **64 failed + 12 errors pré-existentes** (não relacionados, módulos distintos — DB de teste/fixtures, não o gate). Com o fix: 927→931 passed (apenas os 4 testes novos somados). **Zero regressões.**
- **Deploy:** `git push origin main` → Railway rebuildou e os 6 serviços de aplicação confirmaram `SUCCESS` no commit `9dc50a1` (`scalpyn`, `scalpyn-worker-micro`, `scalpyn-worker-structural`, `scalpyn-worker-compute`, `scalpyn-worker-execution`, `scalpyn-beat`). `ML_GATE_ENABLED` permaneceu `false` durante todo o processo — nenhuma mudança de comportamento visível em produção, apenas a correção da base de código.

## 3. Prova decisiva (read-only, pós-deploy, contra dados reais de produção)

Executada após confirmação do deploy, sem nunca setar `ML_GATE_ENABLED=true`, sem escrita no banco:

```python
get_model(model_lane="L3_PROFILE")
# → NoEligibleModelError: "Nenhum modelo active+lane=L3_PROFILE aprovado
#    pelo Promotion Gate. reason_code=NO_ELIGIBLE_MODEL_FOR_LANE"

await WinFastPredictor().predict(
    metrics={...}, db=None, symbol="AUDIT_FASE1_TESTSYM",
    profile_id=None, model_lane="L3_PROFILE",
)
# → {
#     "win_fast_probability": None,
#     "model_approved": False,
#     "threshold_used": None,
#     "model_id": None,
#     "model_lane": "L3_PROFILE",
#     "score_status": "SKIPPED",
#     "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE",
#   }
```

| Asserção | Resultado |
|---|---|
| `model_approved is False` | ✅ PASS |
| `reason_code == "NO_ELIGIBLE_MODEL_FOR_LANE"` | ✅ PASS |
| `win_fast_probability is None` | ✅ PASS |
| `score_status == "SKIPPED"` | ✅ PASS |
| `get_model()` levanta `NoEligibleModelError` (não retorna `None`) | ✅ PASS |

Esta é exatamente a função que `pipeline_scan.py` (`_ml_predict_one`, linhas 2976-3001) chama para cada decisão `ALLOW` dentro do bloco `if _ml_gate_enabled:`. O resultado `model_approved=False` é o único sinal que converte `_d["decision"] = "BLOCK"` (linha 3034-3043). A prova foi feita com o código **já deployado** (commit `9dc50a1` confirmado em todos os 6 serviços) e contra o **estado real do banco de produção** (v44 `REJECTED`, v46 `REJECTED`, nenhum modelo `APPROVED`).

## 4. Veredito da Fase 1

| Critério | Status |
|---|---|
| Lógica de bloqueio (função usada pelo gate) prova fail-closed para modelo REJECTED, inclusive sob chamadas repetidas dentro da janela de cache | ✅ PASS |
| Bug de fail-open encontrado durante a validação foi corrigido, testado e deployado sem regressão | ✅ PASS |
| Observação de um ciclo real e completo do `pipeline_scan` com `ML_GATE_ENABLED=true` gerando `BLOCK`/`reason=ml_gate` em `decisions_log` | ⚠️ PENDENTE — bloqueado por um risco pré-existente e não relacionado (race de DDL entre forks do Celery na reinicialização dos workers, seção 1) |
| Nenhum capital real em risco durante toda a fase | ✅ PASS (0/109 live trading, nenhuma ordem real, confirmado por leitura read-only) |

**Conclusão:** a Fase 1 está **PASS no nível de lógica/unidade** (prova direta, contra dados reais, no código já deployado) e identificou+corrigiu um bug de segurança real antes que ele pudesse mascarar um teste de canário futuro. A prova **end-to-end via observação de um ciclo completo do pipeline** permanece pendente — não porque a lógica do gate esteja incerta, mas porque reproduzi-la exige religar `ML_GATE_ENABLED=true`, o que força um restart de workers e reabre uma janela conhecida (não corrigida) de crash-loop transitório por contenção de DDL entre processos `fork`. Essa decisão (religar a flag novamente, aceitando o risco conhecido e transitório, ou primeiro corrigir a race de DDL) é registrada como pendência para a próxima etapa, não decidida unilateralmente nesta sessão.
