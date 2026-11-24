"""Deterministic E/OU conditions. No eval, code loading, or network access."""

from math import isfinite
from operator import eq, ne, gt, ge, lt, le
from ..schemas.mtf_rules import Condition, DURATIONS

OPS = {"=": eq, "!=": ne, ">": gt, ">=": ge, "<": lt, "<=": le}


def scalar(value):
    return (
        value
        if isinstance(value, (str, bool))
        or isinstance(value, (int, float))
        and isfinite(value)
        else None
    )


def operand(op, values, series, cutoff, *, preview_at=None):
    if op.ref is None:
        return op.value, None
    if op.ref.startswith("indicators."):
        samples = series.get(op.ref, [])
        points = [p for p in samples if p["close_at"] <= cutoff or (
            preview_at is not None and p['time'] <= preview_at < p['close_at'])]
        if not points:
            return None, None
        point = points[-1]
        if point.get("valid_until", cutoff) < cutoff:
            return None, point
        return scalar(point["value"]), point
    value = values
    for part in op.ref.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return scalar(value), None


def evaluate_group(root, values, series, cutoff, *, role="required", preview_at=None):
    checks = []

    def visit(node):
        if not isinstance(node, Condition):
            states = [visit(child) for child in node.conditions]
            states = [x for x in states if x != "DISABLED"]
            # Unknown inputs stay unknown even within OR: missing is never approval.
            if not states:
                return "DISABLED"
            if "UNAVAILABLE" in states:
                return "UNAVAILABLE"
            passed = (
                all(x == "PASS" for x in states)
                if node.logic == "AND"
                else any(x == "PASS" for x in states)
            )
            return "PASS" if passed else "FAIL"
        a, point = operand(node.left, values, series, cutoff, preview_at=preview_at)
        b, _ = operand(
            node.right,
            values,
            series,
            (
                point["close_at"]
                if preview_at is None and point and node.operator.startswith("crosses_")
                else cutoff
            ), preview_at=preview_at,
        )
        state = "DISABLED" if not node.enabled else "UNAVAILABLE"
        previous = None
        if node.enabled and a is not None and b is not None:
            try:
                if node.operator.startswith("crosses_"):
                    history = series.get(node.left.ref, [])
                    previous_points = [
                        p
                        for p in history
                        if point and p["close_at"] < point["close_at"]
                    ]
                    prior = previous_points[-1] if previous_points else None
                    if (
                        prior
                        and point["close_at"] - prior["close_at"]
                        == DURATIONS[point["timeframe"]]
                    ):
                        pa, _ = operand(node.left, values, series, prior["close_at"])
                        pb, _ = operand(node.right, values, series, prior["close_at"])
                        previous = {
                            "actual": pa,
                            "expected": pb,
                            "close_at": prior["close_at"],
                        }
                        if pa is not None and pb is not None:
                            passed = (
                                (pa <= pb and a > b)
                                if node.operator == "crosses_above"
                                else (pa >= pb and a < b)
                            )
                            state = "PASS" if passed else "FAIL"
                else:
                    passed = (
                        b[0] <= a <= b[1]
                        if node.operator == "between"
                        else OPS[node.operator](a, b)
                    )
                    state = "PASS" if passed else "FAIL"
            except (TypeError, ValueError, IndexError):
                pass
        checks.append(
            dict(
                id=node.id,
                label=node.label,
                actual=a,
                expected=b,
                operator=node.operator,
                role=role,
                timeframe=point.get("timeframe") if point else None,
                state=state,
                unit="",
                gap=None,
                note=(
                    None
                    if state != "UNAVAILABLE"
                    else "Dado ausente, inválido ou sem histórico suficiente."
                ),
                left=node.left.model_dump(exclude_none=True),
                right=node.right.model_dump(exclude_none=True),
                configuration=point.get("configuration") if point else None,
                previous=previous,
            )
        )
        return state

    return visit(root), checks
