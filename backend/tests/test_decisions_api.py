import os
import sys
from decimal import Decimal
from datetime import timezone

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.decisions import _parse_iso_datetime, _sanitize_decision
from app.tasks.pipeline_scan import _jsonable


def test_sanitize_decision_defaults_to_all():
    assert _sanitize_decision(None) == "ALL"
    assert _sanitize_decision("allow") == "ALLOW"


def test_sanitize_decision_rejects_invalid_values():
    with pytest.raises(HTTPException):
        _sanitize_decision("maybe")


def test_parse_iso_datetime_supports_date_only_ranges():
    start = _parse_iso_datetime("2026-04-21")
    end = _parse_iso_datetime("2026-04-21", is_end=True)

    assert start.hour == 0
    assert start.minute == 0
    assert start.tzinfo == timezone.utc
    assert end.hour == 23
    assert end.minute == 59


def test_jsonable_converts_decimal_values():
    assert _jsonable({"score": Decimal("82.5")}) == {"score": 82.5}
