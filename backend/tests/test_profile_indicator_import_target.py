from types import SimpleNamespace

import pytest

from app.api.profiles import _select_indicator_update_profile


def _profile(*, is_active: bool):
    return SimpleNamespace(is_active=is_active)


def test_indicator_update_keeps_unique_inactive_match_supported():
    profile = _profile(is_active=False)

    assert _select_indicator_update_profile([profile], "L3_TEST") is profile


def test_indicator_update_prefers_sole_active_duplicate():
    inactive = _profile(is_active=False)
    active = _profile(is_active=True)

    assert _select_indicator_update_profile([inactive, active], "L3_TEST") is active


def test_indicator_update_rejects_multiple_active_duplicates():
    with pytest.raises(ValueError, match="2 active profiles named 'L3_TEST'"):
        _select_indicator_update_profile(
            [_profile(is_active=True), _profile(is_active=True)],
            "L3_TEST",
        )


def test_indicator_update_rejects_multiple_inactive_duplicates():
    with pytest.raises(ValueError, match="2 inactive profiles named 'L3_TEST'"):
        _select_indicator_update_profile(
            [_profile(is_active=False), _profile(is_active=False)],
            "L3_TEST",
        )
