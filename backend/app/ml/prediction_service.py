import logging
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.ml.feature_extractor import FEATURE_COLUMNS, ML_EXCLUDED_FIELDS, extract_features
from app.ml.gcs_model_loader import get_model, NoEligibleModelError
from app.ml.prediction_probability import predict_positive_probability

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.500

VALID_MODEL_LANES = frozenset({"L1_SPECTRUM", "L3_PROFILE"})


def _fail_closed_result(
    *,
    model_lane: str | None,
    reason_code: str,
    reason: str | None = None,
    raw_model_output: float | None = None,
) -> dict:
    result = {
        "win_fast_probability": None,
        "model_approved": False,
        "threshold_used": None,
        "model_id": None,
        "model_version": None,
        "model_lane": model_lane,
        "score_status": (
            "ML_EXCEPTION_FAIL_CLOSED"
            if reason_code == "ML_EXCEPTION_FAIL_CLOSED"
            else "SKIPPED"
        ),
        "reason_code": reason_code,
        "raw_model_output": raw_model_output,
    }
    if reason:
        result["reason"] = reason
    return result


class WinFastPredictor:
    """
    Preditor stateless — compatível com Cloud Run.
    Modelo vive no GCS, carregado via singleton em memória.
    Threshold lido do banco (Zero Hardcode).
    """

    async def _get_threshold(self, db: AsyncSession, model_lane: str | None = None) -> tuple:
        """Busca model_id e threshold do modelo ativo.

        Audit P2-5 fix: sem model_lane, a query original podia retornar o
        modelo de uma lane diferente da pretendida sempre que mais de uma
        lane tivesse modelo active simultaneamente (estado real em produção
        em 2026-06-24: v44/L3_PROFILE e v46/L1_SPECTRUM ambos active). Quando
        model_lane é passado, exige também aprovação no Promotion Gate.
        """
        if model_lane:
            result = await db.execute(text("""
                SELECT id, decision_threshold, version
                FROM ml_models
                WHERE status = 'active'
                  AND model_lane = :lane
                  AND (metrics_json->'promotion_gate'->>'status') = 'APPROVED'
                ORDER BY activated_at DESC
                LIMIT 1
            """), {"lane": model_lane})
        else:
            logger.warning(
                "[ML] _get_threshold() chamado sem model_lane — seleção "
                "lane-agnostic (legado, ambígua com múltiplos modelos active)."
            )
            result = await db.execute(text("""
                SELECT id, decision_threshold, version
                FROM ml_models
                WHERE status = 'active'
                ORDER BY activated_at DESC
                LIMIT 1
            """))
        row = result.fetchone()
        if not row:
            return None, DEFAULT_THRESHOLD, None
        return str(row.id), float(row.decision_threshold), str(row.version)

    async def predict(
        self,
        metrics: dict,
        db: AsyncSession,
        decision_id: int | None = None,
        symbol: str | None = None,
        profile_id: str | None = None,
        model_lane: str | None = None,
    ) -> dict:
        """
        Prediz probabilidade WIN_FAST para um sinal L1/L3.

        Args:
            model_lane: 'L1_SPECTRUM' ou 'L3_PROFILE'. Fortemente recomendado
                em todo caller novo (audit P2-5). None preserva o
                comportamento legado lane-agnostic para callers de
                diagnóstico ainda não migrados.

        Returns:
            {
                "win_fast_probability": 0.73 | None,
                "model_approved": True | False,
                "threshold_used": 0.50 | None,
                "model_id": "uuid" | None,
                "model_lane": "L1_SPECTRUM" | None,
                "score_status": "OK" | "SKIPPED",
                "reason_code": None | "NO_ELIGIBLE_MODEL_FOR_LANE" | "MODEL_ARTIFACT_UNAVAILABLE",
            }
        """
        if model_lane is not None and model_lane not in VALID_MODEL_LANES:
            raise ValueError(f"model_lane inválida: {model_lane!r} — use {sorted(VALID_MODEL_LANES)}")

        # Carrega modelo (GCS cache) — profile-specific if profile_id provided
        try:
            model = get_model(profile_id=profile_id, model_lane=model_lane)
        except NoEligibleModelError as e:
            # Distinct from infra failure — there's simply no APPROVED model for
            # this lane right now. Regra absoluta #15: não inventar score nem
            # usar modelo aleatório. score_status=SKIPPED, não bloqueia decisão
            # real por si só (quem chama decide o que fazer com SKIPPED).
            logger.info("[ML] NO_ELIGIBLE_MODEL_FOR_LANE lane=%s: %s", model_lane, e)
            return _fail_closed_result(
                model_lane=model_lane,
                reason_code="NO_ELIGIBLE_MODEL_FOR_LANE",
                reason=str(e),
            )
        except Exception as e:
            logger.warning(f"Modelo indisponível: {e} — BLOQUEANDO por segurança (fail-closed)")
            return _fail_closed_result(
                model_lane=model_lane,
                reason_code="MODEL_ARTIFACT_UNAVAILABLE",
                reason=str(e),
            )

        # Verify feature column alignment if model stores feature names (Audit P1-21)
        model_feature_names = getattr(model, 'feature_names_in_', None)
        if model_feature_names is not None:
            expected = list(FEATURE_COLUMNS[:len(model_feature_names)])
            actual = list(model_feature_names)
            if expected != actual:
                logger.error(
                    "[ML] Feature column order mismatch! Model expects %s but code has %s",
                    actual[:5], expected[:5],
                )
                return _fail_closed_result(
                    model_lane=model_lane,
                    reason_code="MODEL_SCHEMA_ERROR",
                    reason="feature column order mismatch",
                )

        # Threshold do banco
        try:
            model_id, threshold, model_version = await self._get_threshold(db, model_lane=model_lane)
        except Exception as exc:
            logger.warning("[ML] threshold lookup failed lane=%s: %s", model_lane, exc)
            return _fail_closed_result(
                model_lane=model_lane,
                reason_code="ML_EXCEPTION_FAIL_CLOSED",
                reason=str(exc),
            )
        if not model_id:
            return _fail_closed_result(
                model_lane=model_lane,
                reason_code="NO_ELIGIBLE_MODEL_FOR_LANE",
                reason="no approved model threshold row",
            )

        if metrics is None:
            metrics = {}

        # Extrai e vetoriza features.
        # Task #324 — preserve NaN. XGBoost was treinado com missing=nan;
        # default 0.0 colapsaria "ausente" e "zero real" (ex.: taker_ratio=0
        # = 100% venda) e o runtime divergiria do treino.
        #
        # ML_EXCLUDED_FIELDS — strip leakage fields aqui também (defesa em
        # profundidade, espelha o filtro de extract_features para o caso de
        # callers que bypassem extract_features no futuro).
        if any(k in metrics for k in ML_EXCLUDED_FIELDS):
            metrics = {k: v for k, v in metrics.items() if k not in ML_EXCLUDED_FIELDS}
        features = extract_features(metrics)
        _nan = float("nan")
        # Audit P2-28: assert replaced with RuntimeError (assert is stripped by -O).
        _leaked = ML_EXCLUDED_FIELDS.intersection(FEATURE_COLUMNS)
        if _leaked:
            raise RuntimeError(
                f"ML_EXCLUDED_FIELDS contaminou FEATURE_COLUMNS — abortar inferência. "
                f"Campos vazados: {sorted(_leaked)}"
            )
        X = np.array(
            [[features.get(f, _nan) for f in FEATURE_COLUMNS]],
            dtype="float32",
        )

        # Feature count resolution.
        # CatBoost loaded from binary blob does NOT set n_features_in_ (returns 0).
        # Use _n_inference_features stamped by gcs_model_loader as the authoritative
        # source; fall back to sklearn's n_features_in_ for LightGBM/XGBoost.
        expected_features = getattr(model, "_n_inference_features", None) or None
        if not expected_features:
            expected_features = getattr(model, "n_features_in_", None) or None

        # Determine if this model has CatBoost categorical extra features that require
        # a DataFrame rather than a plain float numpy array for inference.
        _inf_names = getattr(model, "_inference_feature_names", []) or []
        _has_cat_features = bool(
            len(_inf_names) > len(FEATURE_COLUMNS)
            and ({"source_encoded", "profile_id_encoded"} & set(_inf_names[len(FEATURE_COLUMNS):]))
        )

        if _has_cat_features:
            # CatBoost was trained with source_encoded + profile_id_encoded as categorical
            # columns AFTER the base features. Numpy float arrays are rejected — must use
            # a DataFrame with those columns set as integers (CatBoost converts internally).
            import hashlib as _hlib
            import pandas as _pd
            _src_enc_map = {
                "L1_SPECTRUM": 0, "L3": 1, "L3_LAB": 2,
                "L3_REJECTED": 3, "L3_SIMULATED": 4,
            }
            _src = "L3" if (model_lane or "").startswith("L3") else (model_lane or "L3")
            _source_code = _src_enc_map.get(_src, 1)
            _pid_bucket = (
                int(_hlib.md5(profile_id.encode()).hexdigest(), 16) % 9999
                if profile_id else 9999
            )
            # Build full-width numpy array (48 base + extra), then wrap in DataFrame
            n_full = len(_inf_names)
            X_full = np.zeros((1, n_full), dtype="float64")
            X_full[0, :X.shape[1]] = X[0, :]  # copy base features (NaN-safe)
            X_infer = _pd.DataFrame(X_full, columns=_inf_names)
            X_infer["source_encoded"] = _source_code
            X_infer["profile_id_encoded"] = _pid_bucket
            logger.info(
                "[ML] CatBoost DataFrame inference: source_encoded=%d profile_id_encoded=%d lane=%s",
                _source_code, _pid_bucket, model_lane,
            )
        else:
            X_infer = X
            if expected_features is not None and X.shape[1] > expected_features:
                logger.info(
                    "[ML] Model expects %d features, vector has %d — truncating macro features. "
                    "Retrain model to enable macro enrichment in inference.",
                    expected_features, X.shape[1],
                )
                X_infer = X[:, :expected_features]
            elif expected_features is not None and X.shape[1] < expected_features:
                n_pad = expected_features - X.shape[1]
                X_infer = np.concatenate([X, np.zeros((1, n_pad), dtype="float32")], axis=1)
                logger.debug(
                    "[ML] Padded %d feature(s) with 0.0 to match model's %d expected features.",
                    n_pad, expected_features,
                )

        # Predição
        try:
            proba = predict_positive_probability(
                model,
                X_infer,
                model_lane=model_lane,
            )
        except Exception as exc:
            logger.warning("[ML] prediction exception lane=%s: %s", model_lane, exc)
            return _fail_closed_result(
                model_lane=model_lane,
                reason_code="ML_EXCEPTION_FAIL_CLOSED",
                reason=str(exc),
                raw_model_output=getattr(exc, "raw_value", None),
            )
        approved = proba >= threshold

        import math as _math
        result = {
            "win_fast_probability": round(proba, 4),
            "model_approved":       approved,
            "threshold_used":       threshold,
            "model_id":             model_id,
            "model_version":        model_version,
            "model_lane":           model_lane,
            "score_status":         "OK",
            "reason_code":          None,
            # Feature values used for this inference — NaN converted to None for JSON safety.
            # Enables post-hoc replay and drift analysis without recomputing features.
            "features_snapshot": {
                k: (None if isinstance(v, float) and _math.isnan(v) else v)
                for k, v in features.items()
            },
        }

        # Log assíncrono — não bloqueia o pipeline
        if decision_id and model_id:
            try:
                await db.execute(text("""
                    INSERT INTO ml_predictions
                        (model_id, decision_id, symbol,
                         win_fast_probability, model_approved, threshold_used)
                    VALUES
                        (:model_id, :decision_id, :symbol,
                         :probability, :approved, :threshold)
                """), {
                    "model_id":    model_id,
                    "decision_id": decision_id,
                    "symbol":      symbol or "UNKNOWN",
                    "probability": proba,
                    "approved":    approved,
                    "threshold":   threshold,
                })
                await db.commit()
            except Exception as e:
                logger.warning("Erro ao logar predição: %s", e)

        return result


# Instância global stateless
predictor = WinFastPredictor()
