# Backfill direcional em features_snapshot - 2026-07-03

## Resumo

VEREDITO: manter CORRECAO C.

CONFIANCA: ALTA para ausencia de lookahead no writer encontrado; MEDIA para upgrade B, porque G2 mostrou diferencas de distribuicao em parte das features e o alcance atual em producao e 1367 linhas L1_SPECTRUM com `_directional_backfill`, nao 354.

ACAO EXECUTADA: as linhas marcadas continuam elegiveis no dataset, mas as features configuradas em `ml_backfilled_feature_names` viram NaN no build. `features_snapshot` agora tem guard de imutabilidade por trigger em producao e scripts historicos de backfill foram desarmados.

## G1 - Writer e janela

EVIDENCIA verbatim:

```text
backend\scripts\backfill_directional_features.py:147:def _load_closed_candles(engine, symbol: str, snapshot_time: datetime, timeframe: str, exchange: str, limit: int) -> pd.DataFrame:
backend\scripts\backfill_directional_features.py:239:    candles = _load_closed_candles(engine, row["symbol"], snapshot_time, timeframe, exchange, candle_limit)
backend\scripts\backfill_directional_features.py:300:        patch["_directional_backfill"] = {
backend\scripts\backfill_directional_features.py:324:def _closed_window_from_cache(
backend\scripts\backfill_directional_features.py:340:def _update_batch(engine, updates: list[tuple[str, dict[str, Any]]]) -> None:
backend\scripts\backfill_directional_features.py:356:    parser.add_argument("--apply", action="store_true")
```

Trecho decisivo:

```python
WHERE symbol = :symbol
  AND exchange = :exchange
  AND timeframe = :timeframe
  AND time <= (:snapshot_time - (:minutes || ' minutes')::interval)
```

E no caminho cacheado:

```python
cutoff = snapshot_time - pd.Timedelta(minutes=minutes)
window = candles[candles["time"] <= cutoff].tail(candle_limit)
```

VEREDITO: SEM LOOKAHEAD no writer encontrado. Para timeframe 5m, o candle que contem `snapshot_time` fica excluido por `snapshot_time - 5m`.

Fonte: o backfill calcula de `ohlcv` com `exchange='gate.io'`, `timeframe='5m'` por default. O provider vivo usa indicadores derivados do pipeline `ohlcv`/`indicators`: `backend\app\tasks\compute_indicators.py:764` le `ohlcv` `timeframe='5m'`; `backend\app\services\shadow_trade_service.py:2732` usa `get_merged_indicators` para entrada viva. CONFIANCA: MEDIA/ALTA para mesma familia de fonte; nao e literalmente a mesma funcao de captura, mas e o mesmo pipeline OHLCV/indicadores.

Celery: `backend\scripts\backfill_directional_features.py` e script operacional, nao aparece em `backend\app\tasks\celery_app.py`; nao ha schedule Celery registrado. VEREDITO: one-shot/ad-hoc, nao recorrente.

## G2 - Distribuicao backfilled vs vivo proximo

Query original falhou para features booleanas:

```text
psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type numeric: "false"
```

Para booleanas, foi usada a mesma consulta com `CASE true/false -> 1/0`.

| feature | live n | live p50 | live p05..p95 | backfilled n | backfilled p50 | backfilled p05..p95 | veredito |
|---|---:|---:|---:|---:|---:|---:|---|
| adx_slope_3 | 98 | 0.1806 | -1.2145..2.2332 | 354 | 0.4988 | -1.4206..2.4672 | sobreposto |
| rsi_slope_3 | 98 | 0.1066 | -3.8493..3.8521 | 354 | -0.0450 | -5.0742..5.3090 | sobreposto |
| rsi_slope_5 | 98 | 0.2054 | -2.8084..3.5613 | 354 | -0.0767 | -3.9210..3.7528 | leve skew |
| macd_hist_slope_3 | 98 | 0.0038 | -0.1560..0.2344 | 354 | 0.0020 | -0.2667..0.2005 | sobreposto |
| macd_hist_slope_5 | 98 | -0.0008 | -0.3262..0.4875 | 354 | 0.0000 | -0.3326..0.2574 | sobreposto |
| higher_highs_5 | 98 | 0.0000 | 0.0000..1.0000 | 354 | 0.0000 | 0.0000..1.0000 | sobreposto |
| higher_lows_5 | 98 | 0.0000 | 0.0000..1.0000 | 354 | 0.0000 | 0.0000..1.0000 | sobreposto |
| vwap_reclaim_bool | 98 | 0.0000 | 0.0000..0.0000 | 354 | 0.0000 | 0.0000..0.0000 | sobreposto |
| ema21_ema50_distance_pct | 98 | -0.0024 | -3.5755..3.2057 | 354 | 0.3417 | -1.3681..2.1801 | skew |
| di_plus_minus_diff | 98 | -2.8372 | -30.7688..36.4892 | 354 | 5.6575 | -32.4044..48.3187 | skew |

VEREDITO: SKEW MATERIAL/INCONCLUSIVO. As caudas se sobrepoem, mas `ema21_ema50_distance_pct` e `di_plus_minus_diff` deslocam mediana/media; controle live tem n=98. Isso nao sustenta upgrade B automatico.

## G3 - Correcao C

Config aplicada em producao:

```json
{
  "ml_backfill_marker_key": "_directional_backfill",
  "ml_backfilled_feature_names": [
    "adx_slope_3",
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "higher_highs_5",
    "higher_lows_5",
    "vwap_reclaim_bool",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff"
  ]
}
```

Diff relevante:

```text
backend\app\ml\feature_extractor.py:320:    backfilled_feature_names: Optional[List[str]] = None,
backend\app\ml\feature_extractor.py:321:    backfill_marker_key: Optional[str] = None,
backend\app\ml\feature_extractor.py:372:            rows_with_backfill_neutralized += 1
backend\app\ml\feature_extractor.py:412:            "BACKFILL_NEUTRALIZATION|marker=%s|features=%d|rows_with_backfill_neutralized=%d",
ml_trainer\job.py:523:    _backfilled_feature_names = _cfg_list(_ml_cfg, "ml_backfilled_feature_names")
ml_trainer\job.py:524:    _backfill_marker_key = str(_ml_cfg.get("ml_backfill_marker_key") or "")
ml_trainer\job.py:764:                        "rows_with_backfill_neutralized": rows_with_backfill_neutralized,
backend\app\services\ml_challenger_service.py:1269:        backfilled_feature_names = [
backend\app\services\ml_challenger_service.py:1272:        backfill_marker_key = str(ml_config.get("ml_backfill_marker_key") or "")
```

Build real L1 30d:

```json
{
  "records": 3241,
  "df_rows": 3241,
  "rows_with_backfill_neutralized": 1367,
  "backfill_marker_key": "_directional_backfill"
}
```

VEREDITO: CORRECAO C ATIVA. O funil do builder nao removeu linhas por causa do backfill: `records=3241`, `df_rows=3241`; apenas neutralizou as 10 features nas linhas marcadas.

Condicao de upgrade B: somente se decisao humana aceitar G1 SEM LOOKAHEAD + mesma fonte funcional + G2 sem skew material. Como G2 nao esta limpo, recomendacao atual e manter C.

## G4 - Enforcement de pureza

Censo de mutadores relevantes:

```text
backfill_features.py:299: "features_snapshot is immutable after INSERT..."
backend\scripts\backfill_directional_features.py:342: "features_snapshot is immutable after INSERT..."
backend\app\services\shadow_trade_service.py:583/600: INSERT legitimo com features_snapshot
backend\app\tasks\pipeline_scan.py:3100/3113: INSERT legitimo com features_snapshot
backend\app\services\simulation_service.py:265: INSERT legitimo em trade_simulations.features_snapshot
```

`rg` final:

```text
rg -n "UPDATE\s+shadow_trades\s+SET\s+features_snapshot|SET\s+features_snapshot\s*=|features_snapshot = CAST|features_snapshot = COALESCE" ... -> sem matches
```

Guard aplicado:

```text
backend\alembic\versions\127_shadow_features_snapshot_immutability.py:52: CREATE OR REPLACE FUNCTION prevent_shadow_features_snapshot_update()
backend\alembic\versions\127_shadow_features_snapshot_immutability.py:60: RAISE EXCEPTION 'shadow_trades.features_snapshot is immutable after INSERT'
backend\alembic\versions\127_shadow_features_snapshot_immutability.py:72: CREATE TRIGGER trg_shadow_features_snapshot_immutable
```

Producao:

```json
{
  "migration": "applied",
  "alembic_version": "127_shadow_fs_immutable",
  "trigger": [{"tgname": "trg_shadow_features_snapshot_immutable"}]
}
```

Teste do guard com linha sintetica em transacao + rollback:

```json
{
  "user_id_found": true,
  "guard_result": "blocked",
  "error": "(psycopg2.errors.CheckViolation) shadow_trades.features_snapshot is immutable after INSERT",
  "synthetic_rows_after_rollback": 0
}
```

TimescaleDB: `shadow_trades` aparece como relacao comum (`relkind='r'`) e sem indicio de hypertable trigger. Sem limitacao Timescale observada.

Regra documentada:

```text
CLAUDE.md:11:features_snapshot is immutable after INSERT - Retroactive enrichment must use a dedicated column/table with provenance...
```

## G5 - Inventario

A4:

```json
{"violacoes": 0}
```

VEREDITO: geometria SL fecha para a query A4.

Paridade entrada menos saida:

```text
_directional_backfill
_features_captured_at
_features_coverage
_oldest_indicator_age_s
```

Paridade saida menos entrada:

```text
[]
```

Classificacao: `_directional_backfill` e marcador de mutacao historica; `_features_captured_at`, `_features_coverage`, `_oldest_indicator_age_s` sao metadados de captura/qualidade de entrada. VEREDITO: SUPERSET JUSTIFICADO, sem furo de feature de saida faltando.

## G6 - Alcance

Marcador por fonte:

```json
[{"source": "L1_SPECTRUM", "count": 1367}]
```

Marcador em `features_snapshot_exit`:

```json
[]
```

Query literal do prompt para chaves `_` retornou chaves demais porque `_` atua como wildcard no `LIKE`. Query corrigida:

```sql
WHERE key LIKE '\_%' ESCAPE '\'
```

Resultado:

```json
[
  {"key": "_features_captured_at", "count": 3265},
  {"key": "_features_coverage", "count": 3265},
  {"key": "_oldest_indicator_age_s", "count": 3265},
  {"key": "_directional_backfill", "count": 1367}
]
```

VEREDITO: backfill marcado so em L1_SPECTRUM e so em entrada. Outros `_` sao metadados de captura, nao backfill de feature.

## Verificacao

```text
python -m pytest backend\tests\test_ml_directional_features.py -q
7 passed in 3.07s

python -m py_compile backend\app\ml\feature_extractor.py backend\app\services\ml_challenger_service.py ml_trainer\job.py backend\scripts\backfill_directional_features.py backfill_features.py backend\alembic\versions\127_shadow_features_snapshot_immutability.py
exit code 0
```

## Recomendacao final

Manter CORRECAO C. Nao escalar para exclusao A porque G1 nao confirmou lookahead. Nao fazer upgrade B agora porque G2 mostrou skew/inconclusivo e G6 revelou alcance atual maior que o numero inicial citado.
