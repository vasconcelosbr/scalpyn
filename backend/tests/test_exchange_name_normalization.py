import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.exchange_names import display_exchange_name, exchange_name_matches, normalize_exchange_name


def test_normalize_exchange_name_handles_gate_aliases():
    assert normalize_exchange_name("Gate.io") == "gate.io"
    assert normalize_exchange_name(" gateio ") == "gate.io"


def test_display_exchange_name_preserves_pretty_label():
    assert display_exchange_name("gate.io") == "Gate.io"


def test_exchange_name_matches_uses_case_insensitive_canonical_filter():
    from sqlalchemy import column

    expr = exchange_name_matches(column("exchange_name"), "Gate.io")
    compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
    assert compiled == "lower(exchange_name) = 'gate.io'"
