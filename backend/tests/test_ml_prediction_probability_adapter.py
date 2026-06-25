import math

import pytest

from backend.app.ml.prediction_probability import (
    ProbabilityPredictionError,
    predict_positive_probability,
)


class _SklearnLike:
    def __init__(self, output):
        self.output = output

    def predict_proba(self, _features):
        return self.output


class _PredictOnly:
    def __init__(self, output):
        self.output = output

    def predict(self, _features):
        return self.output


class _BrokenModel:
    def predict(self, _features):
        raise RuntimeError("model exploded")


class _NoPredict:
    pass


def test_sklearn_like_predict_proba_2d_positive_column():
    proba = predict_positive_probability(_SklearnLike([[0.2, 0.8]]), [[1.0]])
    assert proba == 0.8


def test_lightgbm_booster_like_predict_1d():
    proba = predict_positive_probability(_PredictOnly([0.73]), [[1.0]])
    assert proba == 0.73


def test_predict_output_2d_single_column():
    proba = predict_positive_probability(_PredictOnly([[0.61]]), [[1.0]])
    assert proba == 0.61


@pytest.mark.parametrize("value", [math.nan, -0.01, 1.01])
def test_invalid_probability_values_raise(value):
    with pytest.raises(ProbabilityPredictionError):
        predict_positive_probability(_PredictOnly([value]), [[1.0]])


def test_model_exception_is_controlled_error():
    with pytest.raises(ProbabilityPredictionError) as exc:
        predict_positive_probability(_BrokenModel(), [[1.0]], model_lane="L1_SPECTRUM")

    assert "RuntimeError lane=L1_SPECTRUM" in str(exc.value)


def test_model_without_predict_or_predict_proba_raises():
    with pytest.raises(ProbabilityPredictionError) as exc:
        predict_positive_probability(_NoPredict(), [[1.0]])

    assert "neither predict_proba nor supported predict" in str(exc.value)

