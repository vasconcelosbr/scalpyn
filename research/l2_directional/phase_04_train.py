"""
Phase 4 — Treino por regressão (split temporal, IC como métrica)
L2 Validação Direcional (v2)

Execução:
    python -m research.l2_directional.phase_04_train  (com DATABASE_URL)

Dependências extras (além de asyncpg):
    pip install xgboost scipy scikit-learn numpy

O que faz:
  1. Carrega ml_experiment_features JOIN ml_experiment_labels
  2. Split temporal: 60% train / 20% val / 20% test (por signal_at)
  3. Pesos de recência (exp(-lambda * age_days)) no train
  4. Treina XGBoost e RandomForest com busca de hiperparâmetros no val set
  5. Métrica principal: Spearman IC (correlação de rank previsão→realizado) no test
  6. EV líquido por decil com IC 95% bootstrap no test set
  7. Salva resultado em ml_experiment_results

Gate GO direcional (pré-registrado):
  IC Spearman > ml.go_spearman_ic (0.03) E p < 0.05 no test set

Gate GO operacional (pré-registrado):
  EV Top 10% > ml.go_topdecile_ev (0.0) E CI inferior > EV base
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import xgboost as xgb
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer

from ._db import connect

# Features que nunca entram no modelo (strings, stubs confirmados)
EXCLUDE_FEATURES = {
    "market_data_source", "market_data_symbol", "taker_source", "taker_window",
    "_features_captured_at", "market_data_confidence",
    "macd_signal", "psar_signal", "psar_trend",
}

# Threshold de qualidade: colunas com > MAX_NAN_PCT% de NaN são descartadas
MAX_NAN_PCT = 0.95


# ── Utilitários ──────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def bootstrap_ci(
    values: np.ndarray,
    n_boot: int = 1000,
    ci: float = 0.95,
    rng: Optional[np.random.Generator] = None,
) -> tuple[float, float]:
    if rng is None:
        rng = np.random.default_rng(42)
    boots = [
        float(np.mean(rng.choice(values, len(values), replace=True)))
        for _ in range(n_boot)
    ]
    alpha = (1 - ci) / 2
    return float(np.percentile(boots, alpha * 100)), float(np.percentile(boots, (1 - alpha) * 100))


def ev_by_bucket(
    y_pred: np.ndarray,
    y_true: np.ndarray,
    min_n: int = 50,
) -> dict:
    order = np.argsort(-y_pred)  # decrescente (melhor predição primeiro)
    n     = len(y_true)
    rng   = np.random.default_rng(42)
    result = {}
    for label, pct in [("top_1pct", 1), ("top_5pct", 5), ("top_10pct", 10),
                        ("top_20pct", 20), ("base_100pct", 100)]:
        k = max(1, int(n * pct / 100))
        idx = order[:k]
        vals = y_true[idx]
        ev   = float(np.mean(vals))
        entry: dict = {"n": int(k), "ev": ev, "pct_positive": float(np.mean(vals > 0)) * 100}
        if k >= min_n:
            ci_lo, ci_hi = bootstrap_ci(vals, rng=rng)
            entry.update({"ci_lo": ci_lo, "ci_hi": ci_hi, "status": "ok"})
        else:
            entry["status"] = "insuficiente"
        result[label] = entry
    return result


# ── Carregamento de dados ────────────────────────────────────────────────────

async def load_data(conn, cutoff):
    rows = await conn.fetch("""
        SELECT
            ef.shadow_trade_id,
            ef.signal_at,
            ef.features_json,
            ef.derived_json,
            el.future_return_30m_net       AS label,
            EXTRACT(EPOCH FROM (NOW() - ef.signal_at)) / 86400.0 AS age_days
        FROM ml_experiment_features ef
        JOIN ml_experiment_labels el ON el.shadow_trade_id = ef.shadow_trade_id
        WHERE el.future_return_30m_net IS NOT NULL
          AND ef.signal_at >= $1
        ORDER BY ef.signal_at ASC
    """, cutoff)

    if not rows:
        return None, None, None, None, None

    # Coleta de todos os nomes de features presentes nos dados
    feat_sets: dict[str, list] = {}
    labels: list[float] = []
    ages: list[float]   = []
    dates: list         = []

    for row in rows:
        labels.append(float(row["label"]))
        ages.append(float(row["age_days"]))
        dates.append(row["signal_at"])

        merged: dict = {}
        for src in (row["features_json"], row["derived_json"]):
            if not src:
                continue
            d = json.loads(src) if isinstance(src, str) else dict(src)
            merged.update(d)

        for k, v in merged.items():
            if k in EXCLUDE_FEATURES:
                continue
            fv = _safe_float(v)
            feat_sets.setdefault(k, [None] * len(labels))
            # Garante tamanho correto (preenche None para linhas anteriores sem essa feature)
            while len(feat_sets[k]) < len(labels):
                feat_sets[k].append(None)
            feat_sets[k][-1] = fv

    # Alinha todos os vetores ao tamanho final
    n = len(labels)
    for k in feat_sets:
        while len(feat_sets[k]) < n:
            feat_sets[k].append(None)

    # Filtra features com > MAX_NAN_PCT% de NaN
    feat_names = []
    for k in sorted(feat_sets.keys()):
        vals = feat_sets[k]
        nan_pct = sum(1 for v in vals if v is None) / n
        if nan_pct <= MAX_NAN_PCT:
            feat_names.append(k)

    # Monta matriz X
    X = np.full((n, len(feat_names)), np.nan)
    for j, k in enumerate(feat_names):
        for i, v in enumerate(feat_sets[k]):
            if v is not None:
                X[i, j] = v

    y        = np.array(labels, dtype=np.float64)
    age_days = np.array(ages,   dtype=np.float64)

    return X, y, age_days, dates, feat_names


# ── Treino e avaliação ───────────────────────────────────────────────────────

def temporal_split(n: int, train_frac: float = 0.60, val_frac: float = 0.20):
    n_train = int(n * train_frac)
    n_val   = int(n * val_frac)
    return (
        np.arange(n_train),
        np.arange(n_train, n_train + n_val),
        np.arange(n_train + n_val, n),
    )


def recency_weights(age_days: np.ndarray, lambda_r: float, min_w: float) -> np.ndarray:
    w = np.exp(-lambda_r * age_days)
    w[w < min_w] = 0.0
    # Normaliza para soma = n (XGBoost espera isso para comparabilidade)
    s = w.sum()
    return w * len(w) / s if s > 0 else np.ones(len(w))


def tune_xgboost(X_tr, y_tr, w_tr, X_val, y_val):
    """Grid search de hiperparâmetros no val set (Spearman IC)."""
    best_ic   = -999.0
    best_params = {}
    best_model  = None

    grid = [
        {"n_estimators": ne, "max_depth": md, "learning_rate": lr, "subsample": ss}
        for ne in [300, 600]
        for md in [3, 5]
        for lr in [0.05, 0.10]
        for ss in [0.8]
    ]
    for p in grid:
        model = xgb.XGBRegressor(
            n_estimators=p["n_estimators"],
            max_depth=p["max_depth"],
            learning_rate=p["learning_rate"],
            subsample=p["subsample"],
            colsample_bytree=0.8,
            min_child_weight=5,
            reg_lambda=1.0,
            tree_method="hist",
            random_state=42,
            verbosity=0,
            n_jobs=-1,
        )
        model.fit(X_tr, y_tr, sample_weight=w_tr,
                  eval_set=[(X_val, y_val)], verbose=False)
        pred = model.predict(X_val)
        ic, _ = spearmanr(pred, y_val)
        if ic > best_ic:
            best_ic, best_params, best_model = ic, p, model

    return best_model, best_params, best_ic


def tune_rf(X_tr, y_tr, w_tr, X_val, y_val, imputer):
    best_ic = -999.0
    best_params = {}
    best_model  = None

    # RF não suporta NaN — usar imputador já fitado no train
    X_tr_imp  = imputer.transform(X_tr)
    X_val_imp = imputer.transform(X_val)

    grid = [
        {"n_estimators": ne, "max_depth": md, "min_samples_leaf": ml}
        for ne in [300, 600]
        for md in [5, 10]
        for ml in [5, 10]
    ]
    for p in grid:
        model = RandomForestRegressor(
            n_estimators=p["n_estimators"],
            max_depth=p["max_depth"],
            min_samples_leaf=p["min_samples_leaf"],
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_tr_imp, y_tr, sample_weight=w_tr)
        pred = model.predict(X_val_imp)
        ic, _ = spearmanr(pred, y_val)
        if ic > best_ic:
            best_ic, best_params, best_model = ic, p, model

    return best_model, best_params, best_ic, imputer


# ── Tabela de resultados ─────────────────────────────────────────────────────

async def ensure_results_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_experiment_results (
            id              BIGSERIAL PRIMARY KEY,
            run_at          TIMESTAMPTZ DEFAULT NOW(),
            phase           TEXT NOT NULL,
            model_name      TEXT,
            split_label     TEXT,
            n_samples       INTEGER,
            spearman_ic     DOUBLE PRECISION,
            spearman_p      DOUBLE PRECISION,
            ev_top10        DOUBLE PRECISION,
            ev_top10_ci_lo  DOUBLE PRECISION,
            ev_top10_ci_hi  DOUBLE PRECISION,
            ev_base         DOUBLE PRECISION,
            pct_positive_top10 DOUBLE PRECISION,
            go_direcional   BOOLEAN,
            go_operacional  BOOLEAN,
            metrics_json    JSONB,
            config_json     JSONB
        )
    """)


def _print_ev_table(label: str, buckets: dict, min_n: int) -> None:
    print(f"\n  EV por decil [{label}]:")
    for bucket, info in buckets.items():
        n    = info["n"]
        ev   = info["ev"]
        pct  = info.get("pct_positive", 0.0)
        status = info.get("status", "")
        if status == "insuficiente":
            print(f"    {bucket:<14} n={n:<5} EV={ev:+.4f}%  [INSUFICIENTE < {min_n}]")
        else:
            lo = info.get("ci_lo", float("nan"))
            hi = info.get("ci_hi", float("nan"))
            print(f"    {bucket:<14} n={n:<5} EV={ev:+.4f}%  CI95=[{lo:+.4f}, {hi:+.4f}]  "
                  f"pos%={pct:.1f}%")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    conn = await connect()

    cfg_row = await conn.fetchrow("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml_research' AND is_active = true LIMIT 1
    """)
    raw_cfg = cfg_row["config_json"] if cfg_row else None
    cfg     = (json.loads(raw_cfg) if isinstance(raw_cfg, str) else dict(raw_cfg)) if raw_cfg else {}

    lookback_days  = int(cfg.get("ml.lookback_days", 90))
    lambda_r       = float(cfg.get("ml.recency_lambda", 0.0231))
    min_w          = float(cfg.get("ml.recency_min_weight", 0.05))
    min_bucket_n   = int(cfg.get("ml.min_bucket_n", 50))
    go_ic_thresh   = float(cfg.get("ml.go_spearman_ic", 0.03))
    go_ev_thresh   = float(cfg.get("ml.go_topdecile_ev", 0.0))
    cutoff         = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    print("=" * 70)
    print("FASE 4 — Treino por Regressão (split temporal)")
    print("L2 Validação Direcional (v2)")
    print(f"Executado em: {datetime.now(timezone.utc).isoformat()}")
    print(f"Split: 60% train / 20% val / 20% test (por signal_at)")
    print(f"GO direcional: Spearman IC > {go_ic_thresh} e p < 0.05 no test")
    print(f"GO operacional: EV Top 10% > {go_ev_thresh} E CI inferior > EV base")
    print("=" * 70)

    await ensure_results_table(conn)

    print("\n  Carregando dados...", flush=True)
    X, y, age_days, dates, feat_names = await load_data(conn, cutoff)

    if X is None:
        print("  [FAIL] Sem dados — rode Phase 3 primeiro.")
        await conn.close()
        return

    n = len(y)
    print(f"  n total [query] = {n}")
    print(f"  features usadas = {len(feat_names)}")
    print(f"  data mais antiga: {dates[0]}")
    print(f"  data mais recente: {dates[-1]}")

    # Split temporal
    idx_tr, idx_val, idx_te = temporal_split(n)
    print(f"\n  Split: train={len(idx_tr)} | val={len(idx_val)} | test={len(idx_te)}")
    if len(idx_te) < min_bucket_n:
        print(f"  [!] Test set ({len(idx_te)}) < min_bucket_n ({min_bucket_n}) — resultado menos confiável")

    X_tr,  y_tr  = X[idx_tr],  y[idx_tr]
    X_val, y_val = X[idx_val], y[idx_val]
    X_te,  y_te  = X[idx_te],  y[idx_te]
    w_tr = recency_weights(age_days[idx_tr], lambda_r, min_w)

    # Imputador para RF (XGBoost suporta NaN nativamente)
    imputer = SimpleImputer(strategy="median")
    imputer.fit(X_tr)

    results_summary = []

    # ── XGBoost ──────────────────────────────────────────────────────
    print("\n[1] XGBoost — grid search no val set...")
    xgb_model, xgb_params, xgb_val_ic = tune_xgboost(X_tr, y_tr, w_tr, X_val, y_val)
    print(f"    Melhor params: {xgb_params}  | val IC={xgb_val_ic:.4f}")

    # Reidenta em train+val para avaliar no test
    idx_trval = np.concatenate([idx_tr, idx_val])
    X_trval   = X[idx_trval]
    y_trval   = y[idx_trval]
    w_trval   = recency_weights(age_days[idx_trval], lambda_r, min_w)
    xgb_final = xgb.XGBRegressor(
        **xgb_params,
        colsample_bytree=0.8,
        min_child_weight=5,
        reg_lambda=1.0,
        tree_method="hist",
        random_state=42,
        verbosity=0,
        n_jobs=-1,
    )
    xgb_final.fit(X_trval, y_trval, sample_weight=w_trval)

    xgb_pred_te = xgb_final.predict(X_te)
    xgb_ic_te, xgb_p_te = spearmanr(xgb_pred_te, y_te)
    xgb_buckets = ev_by_bucket(xgb_pred_te, y_te, min_n=min_bucket_n)

    print(f"\n  [XGBoost — TEST SET]")
    print(f"    Spearman IC [test] = {xgb_ic_te:.4f}  p={xgb_p_te:.4f}")
    _print_ev_table("XGBoost test", xgb_buckets, min_bucket_n)

    # Feature importance (top 20)
    importances = xgb_final.feature_importances_
    top_idx = np.argsort(-importances)[:20]
    print(f"\n  Feature Importance (Gain) — Top 20:")
    for i in top_idx:
        print(f"    {feat_names[i]:<40} {importances[i]:.4f}")

    results_summary.append(("XGBoost", xgb_ic_te, xgb_p_te, xgb_buckets, xgb_params))

    # ── RandomForest ─────────────────────────────────────────────────
    print("\n[2] RandomForest — grid search no val set...")
    rf_model, rf_params, rf_val_ic, imputer = tune_rf(X_tr, y_tr, w_tr, X_val, y_val, imputer)
    print(f"    Melhor params: {rf_params}  | val IC={rf_val_ic:.4f}")

    X_trval_imp = imputer.transform(X_trval)
    X_te_imp    = imputer.transform(X_te)
    rf_final    = RandomForestRegressor(
        **rf_params,
        random_state=42,
        n_jobs=-1,
    )
    rf_final.fit(X_trval_imp, y_trval, sample_weight=w_trval)

    rf_pred_te = rf_final.predict(X_te_imp)
    rf_ic_te, rf_p_te = spearmanr(rf_pred_te, y_te)
    rf_buckets = ev_by_bucket(rf_pred_te, y_te, min_n=min_bucket_n)

    print(f"\n  [RandomForest — TEST SET]")
    print(f"    Spearman IC [test] = {rf_ic_te:.4f}  p={rf_p_te:.4f}")
    _print_ev_table("RF test", rf_buckets, min_bucket_n)

    results_summary.append(("RandomForest", rf_ic_te, rf_p_te, rf_buckets, rf_params))

    # ── Veredito GO ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VEREDITO GO (contra pré-registro)")
    print("=" * 70)
    ev_base_xgb = xgb_buckets["base_100pct"]["ev"]

    for model_name, ic, p_val, buckets, params in results_summary:
        top10 = buckets.get("top_10pct", {})
        ev10  = top10.get("ev", float("nan"))
        ci_lo = top10.get("ci_lo", float("nan"))
        ev_base = buckets["base_100pct"]["ev"]

        go_d = bool(float(ic) > go_ic_thresh and float(p_val) < 0.05)
        go_o = bool(
            top10.get("status") == "ok"
            and float(ev10) > go_ev_thresh
            and float(ci_lo) > float(ev_base)
        )

        print(f"\n  [{model_name}]")
        print(f"    GO Direcional: IC={ic:.4f} > {go_ic_thresh} E p={p_val:.4f} < 0.05 "
              f"→ {'[OK] PASSA' if go_d else '[FAIL]'}")
        print(f"    GO Operacional: EV Top10={ev10:+.4f}% > {go_ev_thresh} "
              f"E CI_lo={ci_lo:+.4f}% > EV_base={ev_base:+.4f}% "
              f"→ {'[OK] PASSA' if go_o else '[FAIL]'}")

        # Salva no DB
        await conn.execute("""
            INSERT INTO ml_experiment_results
                (phase, model_name, split_label, n_samples,
                 spearman_ic, spearman_p,
                 ev_top10, ev_top10_ci_lo, ev_top10_ci_hi, ev_base,
                 pct_positive_top10, go_direcional, go_operacional,
                 metrics_json, config_json)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb)
        """,
            "phase_04", model_name, "test", len(y_te),
            float(ic), float(p_val),
            float(ev10) if not math.isnan(ev10) else None,
            float(ci_lo) if not math.isnan(ci_lo) else None,
            float(top10.get("ci_hi", float("nan"))) if not math.isnan(top10.get("ci_hi", float("nan"))) else None,
            float(ev_base),
            float(top10.get("pct_positive", 0.0)),
            bool(go_d), bool(go_o),
            json.dumps(buckets),
            json.dumps({"params": params, "n_train": len(idx_tr),
                        "n_val": len(idx_val), "n_test": len(idx_te),
                        "n_features": len(feat_names),
                        "go_ic_thresh": go_ic_thresh, "go_ev_thresh": go_ev_thresh}),
        )

    # ── Ledger ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("LEDGER DE EVIDÊNCIAS")
    print("=" * 70)
    print(f"  n_total [query ml_experiment_features] = {n}")
    print(f"  n_train [calc: 60%] = {len(idx_tr)}")
    print(f"  n_val   [calc: 20%] = {len(idx_val)}")
    print(f"  n_test  [calc: 20%] = {len(idx_te)}")
    for model_name, ic, p_val, buckets, _ in results_summary:
        top10 = buckets.get("top_10pct", {})
        ev10 = top10.get("ev", float("nan"))
        print(f"  {model_name}.ic_test  [calc: spearmanr] = {ic:.4f}  p={p_val:.4f}")
        print(f"  {model_name}.ev_top10 [calc: mean top10] = {ev10:+.4f}%")
    print(f"  go_ic_thresh [config ml_research] = {go_ic_thresh}")
    print(f"  go_ev_thresh [config ml_research] = {go_ev_thresh}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
