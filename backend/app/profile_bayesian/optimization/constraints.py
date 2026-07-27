"""Config-driven hard gates for every optimization trial."""

from __future__ import annotations

from typing import Any, Mapping


def constraint_violations(
    metrics: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[str]:
    violations: list[str] = []
    checks = (
        ("n_trades", ">=", "min_trades"),
        ("n_symbols", ">=", "min_symbols"),
        ("n_days", ">=", "min_days"),
        ("max_symbol_concentration", "<=", "max_symbol_concentration"),
        ("max_drawdown", "<=", "max_drawdown"),
        ("expectancy_oos", ">=", "min_expectancy_oos"),
        ("profit_factor_oos", ">=", "min_profit_factor"),
        ("is_oos_degradation", "<=", "max_is_oos_degradation"),
        ("min_regime_samples", ">=", "min_regime_samples"),
    )
    for metric, operator, threshold in checks:
        value = metrics.get(metric)
        if value is None:
            violations.append(f"missing:{metric}")
            continue
        boundary = policy[threshold]
        if operator == ">=" and float(value) < float(boundary):
            violations.append(f"{metric}:below_policy")
        elif operator == "<=" and float(value) > float(boundary):
            violations.append(f"{metric}:above_policy")
    if metrics.get("policy_compatible") is not True:
        violations.append("operational_policy_incompatible")
    return violations
