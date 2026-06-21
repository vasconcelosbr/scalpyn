"""Decision Orchestrator — combina scores L1 (XGBoost) e L3 (CatBoost) em final_priority_score.

Arquitetura 2-lanes:
  L1_SPECTRUM → XGBoost  (p_l1_win)          — "este cripto tem comportamento bruto favorável?"
  L3/L3_LAB   → CatBoost (p_l3_profile_win)  — "este ativo funciona dentro deste profile?"

  final_priority_score = w_l1 * p_l1_win + w_l3 * p_l3_profile_win
                         (fallback para p_l1_win quando CatBoost não disponível)

Semântica de campos em shadow_trades:
  - ml_probability      : score do modelo ORIGINAL da decisão (não sobrescrito aqui)
  - final_priority_score: score combinado L1+L3 (gravado pelo backfill)
  - orchestrator_payload: JSON com p_l1_win, p_l3_profile_win, reason_codes, model_ids

Uso:
  - backfill_orchestrator_scores(): atualiza shadow trades com final_priority_score = NULL
    SEGURO POR PADRÃO: dry_run=True não escreve nada.
  - compute_trade_score(): computa score para um trade individual (sem DB write)
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("scalpyn.services.decision_orchestrator")

DEFAULT_L1_WEIGHT = 0.60
DEFAULT_L3_WEIGHT = 0.40

L3_ELIGIBLE_SOURCES = frozenset({"L3", "L3_LAB"})
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


async def _get_active_l1_model_id(db: AsyncSession) -> Optional[str]:
    """Retorna o id do modelo L1_SPECTRUM ativo, ou None."""
    try:
        row = (await db.execute(text("""
            SELECT id::text FROM ml_models
            WHERE model_lane = 'L1_SPECTRUM'
              AND status = 'active'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        """))).fetchone()
        return row[0] if row else None
    except Exception:
        return None


async def _get_profile_catboost_score(
    db: AsyncSession,
    profile_id: Optional[str],
    features: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Carrega modelo CatBoost do profile e executa inferência.

    Retorna {"score": float, "model_id": str} ou None se modelo indisponível.
    Usa Pool(cat_features=...) com os índices salvos no payload do modelo.
    """
    if not profile_id:
        return None

    try:
        row = (await db.execute(text("""
            SELECT id::text, model_blob, decision_threshold, hyperparams
            FROM ml_models
            WHERE model_scope = 'profile'
              AND profile_id = :pid
              AND status = 'active'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        """), {"pid": str(profile_id)})).fetchone()

        if not row or not row[1]:
            return None

        model_id, model_blob, threshold, hyperparams_raw = row
        hp = hyperparams_raw if isinstance(hyperparams_raw, dict) else json.loads(hyperparams_raw or "{}")
        if "catboost" not in str(hp.get("model_type", "catboost")).lower():
            return None

        import joblib
        import numpy as np

        payload = joblib.load(io.BytesIO(bytes(model_blob)))
        model = payload["model"]
        saved_cols: List[str] = payload.get("feature_columns", [])
        cat_feature_indices: Optional[List[int]] = payload.get("metadata", {}).get("cat_feature_indices")

        from app.ml.feature_extractor import extract_features

        feat_dict = extract_features(features or {})

        _SRC_ENC = {"L1_SPECTRUM": 0, "L3": 1, "L3_LAB": 2, "L3_REJECTED": 3, "L3_SIMULATED": 4}
        feat_dict["source_encoded"] = float(_SRC_ENC.get("L3", 1))
        feat_dict["profile_id_encoded"] = float(abs(hash(str(profile_id))) % 10000)

        X = np.array(
            [[feat_dict.get(c, 0.0) for c in saved_cols]],
            dtype="float32",
        )
        X = np.nan_to_num(X, nan=0.0)

        if cat_feature_indices:
            from catboost import Pool
            pool = Pool(X, feature_names=list(saved_cols), cat_features=cat_feature_indices)
            proba = float(model.predict_proba(pool)[0][1])
        else:
            proba = float(model.predict_proba(X)[0][1])

        return {"score": round(proba, 4), "model_id": model_id}

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
        total = l1_weight + l3_weight
        w1 = l1_weight / total
        w3 = l3_weight / total
        return round(w1 * p_l1_win + w3 * p_l3_profile_win, 4)
    return round(p_l1_win, 4)


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

    p_l3_result: Optional[Dict[str, Any]] = None
    if source in L3_ELIGIBLE_SOURCES:
        p_l3_result = await _get_profile_catboost_score(db, profile_id, features)

    p_l3_win = p_l3_result["score"] if p_l3_result else None

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
    dry_run: bool = True,
    profile_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    only_null_scores: bool = True,
) -> Dict[str, Any]:
    """
    Backfill de final_priority_score + orchestrator_payload para shadow trades sem score.

    SEGURO POR PADRÃO: dry_run=True não escreve nada no banco.

    ml_probability NÃO é sobrescrito — pertence ao modelo original da decisão.
    Os scores orquestradores vão para orchestrator_payload JSONB + final_priority_score.

    orchestrator_payload contém:
        p_l1_win, p_l3_profile_win, reason_codes, l1_model_id, l3_model_id, weights, scored_at

    only_null_scores=True (default): processa apenas trades com final_priority_score IS NULL.
    only_null_scores=False: permite re-score de trades já pontuados (use com cautela).
    """
    from app.ml.prediction_service import predictor

    sources = source_filter or list(L3_ELIGIBLE_SOURCES | L1_ONLY_SOURCES)
    source_placeholders = ", ".join(f":src_{i}" for i in range(len(sources)))
    source_params = {f"src_{i}": s for i, s in enumerate(sources)}

    extra_filters: List[str] = []
    params: Dict[str, Any] = {"uid": user_id, "lim": limit, **source_params}

    if only_null_scores:
        extra_filters.append("AND final_priority_score IS NULL")
    if profile_id:
        extra_filters.append("AND profile_id = CAST(:profile_id_filter AS UUID)")
        params["profile_id_filter"] = profile_id
    if from_date:
        extra_filters.append("AND created_at >= CAST(:from_date AS TIMESTAMPTZ)")
        params["from_date"] = from_date
    if to_date:
        extra_filters.append("AND created_at <= CAST(:to_date AS TIMESTAMPTZ)")
        params["to_date"] = to_date

    extra_sql = " ".join(extra_filters)

    rows = (await db.execute(text(f"""
        SELECT
            id::text          AS trade_id,
            source,
            profile_id::text  AS profile_id,
            features_snapshot,
            symbol
        FROM shadow_trades
        WHERE user_id = :uid
          AND source IN ({source_placeholders})
          AND features_snapshot IS NOT NULL
          AND features_snapshot::text <> '{{}}'
          AND status IN ('RUNNING', 'CLOSED', 'TP_HIT', 'SL_HIT', 'TIMEOUT')
          {extra_sql}
        ORDER BY created_at DESC
        LIMIT :lim
    """), params)).fetchall()

    if not rows:
        return {
            "dry_run": dry_run,
            "updated": 0,
            "pending_found": 0,
            "message": "Nenhum trade pendente encontrado.",
        }

    weights = await _load_orchestrator_weights(db, user_id)
    l1_model_id = await _get_active_l1_model_id(db)
    scored_at = datetime.now(timezone.utc).isoformat()

    would_update = 0
    updated = 0
    errors = 0
    l1_unavailable_count = 0
    sample_results: List[Dict[str, Any]] = []

    for row in rows:
        trade_id = row.trade_id
        source = row.source
        profile_id_val = row.profile_id
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
            reason_codes: List[str] = []
            pred = await predictor.predict(features, db)
            p_l1_win = pred.get("win_fast_probability")
            if p_l1_win is None:
                reason_codes.append("L1_MODEL_UNAVAILABLE")
                l1_unavailable_count += 1
                errors += 1
                logger.warning(
                    "[Orchestrator] L1_MODEL_UNAVAILABLE trade=%s source=%s reason_codes=%s",
                    trade_id, source, reason_codes,
                )
                continue

            p_l3_result: Optional[Dict[str, Any]] = None
            l3_model_id: Optional[str] = None

            if source in L3_ELIGIBLE_SOURCES:
                p_l3_result = await _get_profile_catboost_score(db, profile_id_val, features)
                if p_l3_result is None:
                    reason_codes.append("L3_MODEL_UNAVAILABLE")
                else:
                    l3_model_id = p_l3_result.get("model_id")

            p_l3_win = p_l3_result["score"] if p_l3_result else None

            final_score = _combine_scores(
                p_l1_win, p_l3_win,
                weights["l1_weight"], weights["l3_weight"],
                source,
            )

            lane = "L1_L3_COMBINED" if p_l3_win is not None else "L1_ONLY"
            reason_codes.append(lane)

            orchestrator_payload = {
                "p_l1_win": round(p_l1_win, 4),
                "p_l3_profile_win": p_l3_win,
                "final_priority_score": final_score,
                "l1_model_id": l1_model_id,
                "l3_model_id": l3_model_id,
                "reason_codes": reason_codes,
                "weights": weights,
                "scored_at": scored_at,
            }

            entry = {
                "trade_id": trade_id,
                "source": source,
                "symbol": row.symbol,
                "p_l1_win": round(p_l1_win, 4),
                "p_l3_profile_win": p_l3_win,
                "final_priority_score": final_score,
                "lane": lane,
            }

            if dry_run:
                would_update += 1
                if len(sample_results) < 10:
                    sample_results.append(entry)
            else:
                await db.execute(text("""
                    UPDATE shadow_trades
                    SET final_priority_score = :fps,
                        orchestrator_payload  = CAST(:payload AS JSONB)
                    WHERE id = CAST(:sid AS UUID)
                """), {
                    "fps": final_score,
                    "payload": json.dumps(orchestrator_payload),
                    "sid": trade_id,
                })
                updated += 1
                if len(sample_results) < 10:
                    sample_results.append(entry)

        except Exception as exc:
            logger.warning("[Orchestrator] Erro no trade %s: %s", trade_id, exc)
            errors += 1

    if not dry_run:
        try:
            await db.commit()
        except Exception as e:
            logger.error("[Orchestrator] Commit falhou: %s", e)
            return {"updated": 0, "errors": errors, "commit_failed": True, "dry_run": False}

        try:
            await db.execute(text("""
                INSERT INTO profile_intelligence_audit_log (
                    user_id, event_type, payload_json, created_at
                ) VALUES (
                    CAST(:uid AS UUID), 'orchestrator_backfill', CAST(:meta AS JSONB), NOW()
                )
            """), {
                "uid": user_id,
                "meta": json.dumps({
                    "updated": updated,
                    "errors": errors,
                    "l1_unavailable_count": l1_unavailable_count,
                    "pending_found": len(rows),
                    "sources": sources,
                    "weights": weights,
                    "l1_model_id": l1_model_id,
                    "dry_run": False,
                }),
            })
            await db.commit()
        except Exception as e:
            logger.warning("[Orchestrator] Audit trail falhou: %s", e)

    action_count = would_update if dry_run else updated
    logger.info(
        "[Orchestrator] Backfill %s: %s=%d errors=%d total=%d",
        "simulado" if dry_run else "concluído",
        "would_update" if dry_run else "updated",
        action_count, errors, len(rows),
    )

    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "errors": errors,
        "l1_unavailable_count": l1_unavailable_count,
        "pending_found": len(rows),
        "weights": weights,
        "sample_results": sample_results,
    }
    if dry_run:
        result["would_update"] = would_update
        result["message"] = (
            f"Modo dry_run=True: {would_update} trades seriam atualizados. "
            "Passe dry_run=False para efetivar."
        )
    else:
        result["updated"] = updated

    return result


async def get_orchestrator_status(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    """
    Retorna métricas de cobertura + disponibilidade de modelos.

    Readiness:
      READY_FULL      — L1 e L3 ativos
      READY_L1_ONLY   — apenas L1 ativo
      NOT_READY       — nenhum modelo ativo
      NO_COVERAGE     — sem shadow trades nos últimos 30 dias
    """
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

    l1_model: Optional[Dict[str, Any]] = None
    l3_model: Optional[Dict[str, Any]] = None

    try:
        row = (await db.execute(text("""
            SELECT id::text, version, roc_auc, activated_at
            FROM ml_models
            WHERE model_lane = 'L1_SPECTRUM'
              AND status = 'active'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        """))).fetchone()
        if row:
            l1_model = {
                "id": row[0],
                "version": row[1],
                "roc_auc": float(row[2]) if row[2] is not None else None,
                "activated_at": row[3].isoformat() if row[3] else None,
            }
    except Exception as e:
        logger.warning("[Orchestrator] Falha ao checar L1 model: %s", e)

    try:
        row = (await db.execute(text("""
            SELECT id::text, version, roc_auc, activated_at
            FROM ml_models
            WHERE model_lane = 'L3_PROFILE'
              AND status = 'active'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        """))).fetchone()
        if row:
            l3_model = {
                "id": row[0],
                "version": row[1],
                "roc_auc": float(row[2]) if row[2] is not None else None,
                "activated_at": row[3].isoformat() if row[3] else None,
            }
    except Exception as e:
        logger.warning("[Orchestrator] Falha ao checar L3 model: %s", e)

    l1_available = l1_model is not None
    l3_available = l3_model is not None
    total_trades = sum(r["total"] for r in coverage)

    if total_trades == 0:
        readiness = "NO_COVERAGE"
    elif l1_available and l3_available:
        readiness = "READY_FULL"
    elif l1_available:
        readiness = "READY_L1_ONLY"
    else:
        readiness = "NOT_READY"

    return {
        "readiness": readiness,
        "l1_model": l1_model,
        "l3_model": l3_model,
        "l1_available": l1_available,
        "l3_available": l3_available,
        "coverage": coverage,
        "total_trades_30d": total_trades,
    }
