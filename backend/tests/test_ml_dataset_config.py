from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ml.dataset_config import (
    MLDatasetConfigError,
    parse_required_ml_dataset_valid_from,
)


def test_parse_required_ml_dataset_valid_from_accepts_iso_utc_string():
    parsed = parse_required_ml_dataset_valid_from(
        {"ml_dataset_valid_from": "2026-06-14T21:33:10+00:00"}
    )

    assert parsed == datetime(2026, 6, 14, 21, 33, 10, tzinfo=timezone.utc)


def test_parse_required_ml_dataset_valid_from_fails_when_absent():
    with pytest.raises(MLDatasetConfigError):
        parse_required_ml_dataset_valid_from({})


def test_parse_required_ml_dataset_valid_from_fails_when_invalid():
    with pytest.raises(MLDatasetConfigError):
        parse_required_ml_dataset_valid_from({"ml_dataset_valid_from": "not-a-date"})
