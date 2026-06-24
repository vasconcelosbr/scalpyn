import logging
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.ml.feature_extractor import FEATURE_COLUMNS, ML_EXCLUDED_FIELDS, extract_features
from app.ml.gcs_model_loader import get_model, NoEligibleModelError
from app.ml.macro_client import fetch_macro_context

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.500

VALID_MODEL_LANES = frozenset({"L1_SPECTRUM", "L3_PROFILE"})


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
                SELECT id, decision_threshold
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
                SELECT id, decision_threshold
                FROM ml_models
                WHERE status = 'active'
                ORDER BY activated_at DESC
                LIMIT 1
            """))
        row = result.fetchone()
        if not row:
            return None, DEFAULT_THRESHOLD
        return str(row.id), float(row.decision_threshold)

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
                "reason_code": None | "NO_ELIGIBLE_MODEL_FOR_LANE" | "model_unavailable_fail_closed",
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
            return {
                "win_fast_probability": None,
                "model_approved": False,
                "threshold_used": None,
                "model_id": None,
                "model_version": None,
                "model_lane": model_lane,
                "score_status": "SKIPPED",
                "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE",
            }
        except Exception as e:
            logger.warning(f"Modelo indisponível: {e} — BLOQUEANDO por segurança (fail-closed)")
            return {
                "win_fast_probability": None,
                "model_approved": False,
                "threshold_used": None,
                "model_id": None,
                "model_version": None,
                "model_lane": model_lane,
                "score_status": "SKIPPED",
                "reason_code": "model_unavailable_fail_closed",
                "reason": "model_unavailable_fail_closed",
            }

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

        # Threshold do banco
        model_id, threshold = await self._get_threshold(db, model_lane=model_lane)

        # ── Macro enrichment (Market Data Hub) ──────────────────────────────
        # Fetch global macro context concurrently. Never blocks inference on
        # failure — on timeout/error returns macro_context_available=False and
        # all numeric features as None (treated as NaN by XGBoost missing=nan).
        try:
            macro = await fetch_macro_context()
        except Exception as _macro_exc:
            logger.warning("[ML] macro_client failed: %s — proceeding without macro", _macro_exc)
            macro = {"macro_context_available": False}

        # Merge macro features into metrics (additive — never overwrite symbol indicators).
        # Macro keys are at the END of FEATURE_COLUMNS so they never collide with
        # existing indicator features (e.g. rsi, adx).
        if metrics is None:
            metrics = {}
        metrics = {**metrics, **macro}

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

        # Backwards-compat: models trained before macro features were added to
        # FEATURE_COLUMNS expect fewer columns. Truncate X to what the model
        # was trained with — macro features appear at the end of FEATURE_COLUMNS
        # so truncation is safe. When the model is retrained with macro data,
        # n_features_in_ matches len(FEATURE_COLUMNS) and this branch is skipped.
        expected_features = getattr(model, "n_features_in_", None)
        if expected_features is not None and X.shape[1] > expected_features:
            logger.info(
                "[ML] Model expects %d features, vector has %d — truncating macro features. "
                "Retrain model to enable macro enrichment in inference.",
                expected_features, X.shape[1],
            )
            X = X[:, :expected_features]
        elif expected_features is not None and X.shape[1] < expected_features:
            # Forward-compat: model was trained with INCLUDE_REJECTED_IN_TRAIN=true,
            # which appends 'was_rejected' as the last feature column in trainer.py.
            # During live inference all L3 candidates are non-rejected (was_rejected=0).
            n_pad = expected_features - X.shape[1]
            X = np.concatenate([X, np.zeros((1, n_pad), dtype="float32")], axis=1)
            logger.debug(
                "[ML] Padded %d feature(s) with 0.0 (was_rejected=0 for L3 inference) "
                "to match model's %d expected features.",
                n_pad, expected_features,
            )

        # Predição
        proba = float(model.predict_proba(X)[0][1])
        approved = proba >= threshold

        result = {
            "win_fast_probability": round(proba, 4),
            "model_approved":       approved,
            "threshold_used":       threshold,
            "model_id":             model_id,
            "model_lane":           model_lane,
            "score_status":         "OK",
            "reason_code":          None,
            # Macro context returned so callers can persist it to decisions_log.metrics
            # for future ML training without re-fetching. Internal flags stripped.
            "macro_context": {k: v for k, v in macro.items() if k != "macro_context_available"},
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
                logger.warning(f"Erro ao logar predição: {e}")

        return result


# Instância global stateless
predictor = WinFastPredictor()
