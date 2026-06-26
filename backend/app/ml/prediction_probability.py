"""Probability extraction adapters for runtime ML inference."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


class ProbabilityPredictionError(RuntimeError):
    """Raised when a model cannot produce a valid positive-class probability."""
    def __init__(self, message: str, *, raw_value: float | None = None) -> None:
        super().__init__(message)
        self.raw_value = raw_value


def _positive_probability_from_output(output: Any) -> float:
    arr = np.asarray(output)
    if arr.size == 0:
        raise ProbabilityPredictionError("empty prediction output")

    if arr.ndim == 0:
        value = float(arr.item())
    elif arr.ndim == 1:
        value = float(arr[-1] if arr.shape[0] == 2 else arr[0])
    else:
        row = arr[0]
        value = float(row[1] if np.asarray(row).shape[0] > 1 else row[0])

    if not math.isfinite(value):
        raise ProbabilityPredictionError(f"non-finite probability: {value!r}", raw_value=value)
    if value < 0.0 or value > 1.0:
        raise ProbabilityPredictionError(f"probability outside [0, 1]: {value!r}", raw_value=value)
    return value


def predict_positive_probability(
    model: Any,
    features: Any,
    *,
    model_lane: str | None = None,
    model_type: str | None = None,
) -> float:
    """Return the positive-class probability from common model runtimes."""
    module = getattr(model.__class__, "__module__", "")
    name = getattr(model.__class__, "__name__", "")
    type_hint = (model_type or "").lower()
    qualname = f"{module}.{name}".lower()

    try:
        if hasattr(model, "predict_proba"):
            return _positive_probability_from_output(model.predict_proba(features))

        if "xgboost" in type_hint or "xgboost" in qualname:
            try:
                import xgboost as xgb  # type: ignore
            except Exception as exc:  # pragma: no cover - dependency-specific
                raise ProbabilityPredictionError(f"xgboost unavailable: {exc}") from exc
            return _positive_probability_from_output(model.predict(xgb.DMatrix(features)))

        if hasattr(model, "predict"):
            return _positive_probability_from_output(model.predict(features))
    except ProbabilityPredictionError:
        raise
    except Exception as exc:
        lane = f" lane={model_lane}" if model_lane else ""
        raise ProbabilityPredictionError(
            f"{type(exc).__name__}{lane}: {exc}"
        ) from exc

    raise ProbabilityPredictionError(
        f"model has neither predict_proba nor supported predict: {module}.{name}"
    )

