from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND = Path(__file__).resolve().parents[1]


def test_shadow_measurement_migration_is_the_only_head() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["204_shadow_exit_measurement"]


def test_shadow_measurement_migration_is_additive_and_reversible() -> None:
    source = (BACKEND / "alembic" / "versions" / "204_shadow_exit_measurement_contract.py").read_text()

    for column in (
        "exit_price_nominal",
        "exit_price_observed",
        "exit_price_semantics",
        "barrier_overshoot_pct",
        "mfe_mae_source",
        "mfe_mae_recomputed_at",
        "mfe_mae_method_version",
    ):
        assert f'op.add_column(' in source
        assert f'op.drop_column(' in source
        assert column in source
    assert "UPDATE shadow_trade_measurement_revisions" not in source
