"""Decision Orchestrator — combina scores L1 (XGBoost) e L3 (CatBoost) em final_priority_score.

Arquitetura 2-lanes:
  L1_SPECTRUM → XGBoost  (p_l1_win)          — "este cripto tem comportamento bruto favorável?"
  L3/L3_LAB   → CatBoost (p_l3_profile_win)  — "este ativo funciona dentro deste profile?"

  final_priority_score = w_l1 * p_l1_win + w_l3 * p_l3_profile_win
                         (fallback para p_l1_win quando CatBoost não disponível)

Uso:
  - backfill_orchestrator_scores(): atualiza shadow trades com final_priority_score = NULL
  - compute_trade_score(): computa score para um trade individual (sem DB write)
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("scalpyn.services.decision_orchestrator")

# Pesos padrão (configuráveis via config_profiles config_type='orchestrator_weights')
DEFAULT_L1_WEIGHT = 0.60
DEFAULT_L3_WEIGHT = 0.40

# Fontes que recebem score completo (L1 + L3 se profile disponível)
L3_ELIGIBLE_SOURCES = frozenset({"L3", "L3_LAB"})
# Fontes que recebem apenas score L1
L1_ONLY_SOURCES = frozenset({"L1_SPECTRUM"})


async def _load_orchestrator_weights(db: AsyncSession, user_id: str) -> Dict[str, float]:
    """Carrega pesos do banco; retorna defaults se não configurado."""
    try:
        row = (await db.execute(text("""
            SELECT config_json FROM config_profiles
            WHERE user_id = :uid AND config_type = 'orchestrator_weights'
              AND is_active = TRUE
            LIMIT 1
        """), {"uid": user_id})).fetchone()
        if row:
            cfg = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return {
                "l1_weight": float(cfg.get("l1_weight", DEFAULT_L1_WEIGHT)),
                "l3_weight": float(cfg.get("l3_weight", DEFAULT_L3_WEIGHT)),
            }
    except Exception as e:
        logger.warning("[Orchestrator] Falha ao carregar pesos: %s — usando defaults", e)
    return {"l1_weight": DEFAULT_L1_WEIGHT, "l3_weight": DEFAULT_L3_WEIGHT}


async def _get_profile_catboost_score(
    db: AsyncSession,
    profile_id: Optional[str],
    features: Dict[str, Any],
) -> Optional[float]:
    """
    Carrega modelo CatBoost do profile e executa inferência.
    Retorna None se não há modelo disponível ou inferência falha.
    """
    if not profile_id:
        return None

    try:
        row = (await db.execute(text("""
            SELECT model_blob, decision_threshold, hyperparams
            FROM ml_models
            WHERE model_scope = 'profile'
              AND profile_id = :pid
              AND status = 'active'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        """), {"pid": str(profile_id)})).fetchone()

        if not row or not row[0]:
            return None

        model_blob, threshold, hyperparams_raw = row
        hp = hyperparams_raw if isinstance(hyperparams_raw, dict) else json.loads(hyperparams_raw or "{}")
        # Só usar modelo CatBoost nesta lane
        if "catboost" not in str(hp.get("model_type", "catboost")).lower():
            return None

        import joblib
        import numpy as np

        payload = joblib.load(io.BytesIO(bytes(model_blob)))
        model = payload["model"]
        saved_cols: List[str] = payload.get("feature_columns", [])

        # Extrair features no schema do modelo salvo
        from app.ml.feature_extractor import extract_features

        feat_dict = extract_features(features or {})

        # source_encoded e profile_id_encoded — appendados no treinamento CatBoost L3
        _SRC_ENC = {"L1_SPECTRUM": 0, "L3": 1, "L3_LAB": 2, "L3_REJECTED": 3, "L3_SIMULATED": 4}
        feat_dict["source_encoded"] = float(_SRC_ENC.get("L3", 1))
        feat_dict["profile_id_encoded"] = float(abs(hash(str(profile_id))) % 10000)

        X = np.array(
            [[feat_dict.get(c, 0.0) for c in saved_cols]],
            dtype="float32",
        )
        X = np.nan_to_num(X, nan=0.0)

        proba = float(model.predict_proba(X)[0][1])
        return round(proba, 4)

    except Exception as exc:
        logger.warning("[Orchestrator] CatBoost inference falhou para profile=%s: %s", profile_id, exc)
        return None


def _combine_scores(
    p_l1_win: float,
    p_l3_profile_win: Optional[float],
    l1_weight: float,
    l3_weight: float,
    source: str,
) -> float:
    """Combina scores L1 e L3 em final_priority_score."""
    if p_l3_profile_win is not None and source in L3_ELIGIBLE_SOURCES:
        # Normaliza pesos para somar 1.0
        total = l1_weight + l3_weight
        w1 = l1_weight / total
        w3 = l3_weight / total
        return round(w1 * p_l1_win + w3 * p_l3_profile_win, 4)
    return round(p_l1_win, 4)  # fallback: apenas L1


async def compute_trade_score(
    db: AsyncSession,
    user_id: str,
    profile_id: Optional[str],
    features: Dict[str, Any],
    p_l1_win: float,
    source: str,
) -> Dict[str, Any]:
    """
    Computa final_priority_score para um trade individual (sem DB write).

    Args:
        p_l1_win: Probabilidade XGBoost global (já calculada pelo prediction_service)
        source: Fonte do shadow trade (L3, L1_SPECTRUM, etc.)

    Returns:
        {
            "final_priority_score": float,
            "p_l1_win": float,
            "p_l3_profile_win": float | None,
            "weights": {"l1": float, "l3": float},
            "lane": "L1_ONLY" | "L1_L3_COMBINED",
        }
    """
    weights = await _load_orchestrator_weights(db, user_id)

    p_l3_win: Optional[float] = None
    if source in L3_ELIGIBLE_SOURCES:
        p_l3_win = await _get_profile_catboost_score(db, profile_id, features)

    final_score = _combine_scores(
        p_l1_win, p_l3_win,
        weights["l1_weight"], weights["l3_weight"],
        source,
    )

    return {
        "final_priority_score": final_score,
        "p_l1_win": round(p_l1_win, 4),
        "p_l3_profile_win": p_l3_win,
        "weights": weights,
        "lane": "L1_L3_COMBINED" if p_l3_win is not None else "L1_ONLY",
    }


async def backfill_orchestrator_scores(
    db: AsyncSession,
    user_id: str,
    limit: int = 300,
    source_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Backfill de final_priority_score para shadow trades sem score.

    Busca trades com features_snapshot preenchido mas final_priority_score = NULL,
    executa inferência XGBoost (Lane 1) + CatBoost de profile (Lane 2) se disponível,
    e grava final_priority_score + ml_probability em batch.

    Seguro para executar repetidamente (idempotente via WHERE final_priority_score IS NULL).
    """
    from app.ml.prediction_service import predictor

    sources = source_filter or list(L3_ELIGIBLE_SOURCES | L1_ONLY_SOURCES)
    source_placeholders = ", ".join(f":src_{i}" for i in range(len(sources)))
    source_params = {f"src_{i}": s for i, s in enumerate(sources)}

    rows = (await db.execute(text(f"""
        SELECT
            id::text          AS trade_id,
            source,
            profile_id::text  AS profile_id,
            features_snapshot
        FROM shadow_trades
        WHERE user_id = :uid
          AND source IN ({source_placeholders})
          AND features_snapshot IS NOT NULL
          AND features_snapshot::text <> '{{}}'
          AND final_priority_score IS NULL
          AND status IN ('RUNNING', 'CLOSED', 'TP_HIT', 'SL_HIT', 'TIMEOUT')
        ORDER BY created_at DESC
        LIMIT :lim
    """), {"uid": user_id, "lim": limit, **source_params})).fetchall()

    if not rows:
        return {"updated": 0, "pending": 0}

    weights = await _load_orchestrator_weights(db, user_id)
    updated = 0
    errors = 0

    for row in rows:
        trade_id = row.trade_id
        source = row.source
        profile_id = row.profile_id
        raw_features = row.features_snapshot
        features: Dict[str, Any] = {}
        if raw_features:
            if isinstance(raw_features, str):
                try:
                    features = json.loads(raw_features)
                except Exception:
                    features = {}
            elif isinstance(raw_features, dict):
                features = raw_features

        try:
            # Lane 1: XGBoost global
            pred = await predictor.predict(features, db)
            p_l1_win = pred.get("win_fast_probability")
            if p_l1_win is None:
                errors += 1
                continue

            # Lane 2: CatBoost profile (se elegível)
            p_l3_win: Optional[float] = None
            if source in L3_ELIGIBLE_SOURCES:
                p_l3_win = await _get_profile_catboost_score(db, profile_id, features)

            final_score = _combine_scores(
                p_l1_win, p_l3_win,
                weights["l1_weight"], weights["l3_weight"],
                source,
            )

            await db.execute(text("""
                UPDATE shadow_trades
                SET ml_probability       = :p_l1,
                    final_priority_score = :fps
                WHERE id = :sid::uuid
            """), {
                "p_l1": round(p_l1_win, 4),
                "fps": final_score,
                "sid": trade_id,
            })
            updated += 1

        except Exception as exc:
            logger.warning("[Orchestrator] Erro no trade %s: %s", trade_id, exc)
            errors += 1

    try:
        await db.commit()
    except Exception as e:
        logger.error("[Orchestrator] Commit falhou: %s", e)
        return {"updated": 0, "errors": errors, "commit_failed": True}

    logger.info(
        "[Orchestrator] Backfill concluído: updated=%d errors=%d total=%d",
        updated, errors, len(rows),
    )
    return {
        "updated": updated,
        "errors": errors,
        "pending_found": len(rows),
        "weights": weights,
    }


async def get_orchestrator_status(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    """Retorna métricas de cobertura do final_priority_score."""
    rows = (await db.execute(text("""
        SELECT
            source,
            COUNT(*) AS total,
            COUNT(final_priority_score) AS with_score,
            COUNT(ml_probability)       AS with_ml_prob,
            ROUND(AVG(final_priority_score)::numeric, 4) AS avg_score,
            ROUND(AVG(ml_probability)::numeric, 4)       AS avg_ml_prob
        FROM shadow_trades
        WHERE user_id = :uid
          AND source IN ('L3', 'L3_LAB', 'L1_SPECTRUM')
          AND created_at >= NOW() - INTERVAL '30 days'
        GROUP BY source
        ORDER BY total DESC
    """), {"uid": user_id})).fetchall()

    coverage = [
        {
            "source": r.source,
            "total": r.total,
            "with_score": r.with_score,
            "coverage_pct": round(r.with_score / r.total * 100, 1) if r.total else 0.0,
            "with_ml_prob": r.with_ml_prob,
            "avg_score": float(r.avg_score) if r.avg_score is not None else None,
            "avg_ml_prob": float(r.avg_ml_prob) if r.avg_ml_prob is not None else None,
        }
        for r in rows
    ]

    return {
        "coverage": coverage,
        "catboost_available": False,  # será True quando ml_models tiver profile CatBoost ativo
    }
