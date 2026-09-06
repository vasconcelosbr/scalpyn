import pytest

from scripts.build_mtf_waiver_activation_document import _validity_margin_seconds


def test_validity_margin_covers_actual_runtime_consumer_cadence():
    assert _validity_margin_seconds(283.068006, 30, 300) == 584


@pytest.mark.parametrize(
    ("configured_interval", "runtime_interval"),
    [(0, 300), (30, 0)],
)
def test_validity_margin_rejects_missing_operational_cadence(
    configured_interval: int,
    runtime_interval: int,
):
    with pytest.raises(ValueError):
        _validity_margin_seconds(100.0, configured_interval, runtime_interval)
