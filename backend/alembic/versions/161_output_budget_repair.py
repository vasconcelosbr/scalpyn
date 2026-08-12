"""Idempotently repair environments stamped past the output-budget migration.

Revision ID: 161_output_budget_repair
Revises: 160_systemic_output_budget

The API boot fallback can stamp a failed data migration. Re-running the
idempotent revision here makes a correctly migrated environment a no-op and
repairs an environment where revision 160 was stamped without its transaction.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


revision = "161_output_budget_repair"
down_revision = "160_systemic_output_budget"
branch_labels = None
depends_on = None


def _revision(filename: str, module_name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("SYSTEMIC_OUTPUT_BUDGET_MIGRATION_NOT_LOADABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    _revision(
        "159_chat_prompt_repair.py",
        "migration_159_chat_prompt_repair_repair",
    ).upgrade()
    _revision(
        "160_systemic_output_budget.py",
        "migration_160_systemic_output_budget_repair",
    ).upgrade()


def downgrade() -> None:
    # Revision 160 owns the reversible prompt/profile change. This repair
    # marker adds no independent state and therefore has no downgrade action.
    pass
