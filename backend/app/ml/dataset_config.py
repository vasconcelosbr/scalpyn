"""Shared ML dataset configuration guards."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


class MLDatasetConfigError(RuntimeError):
    """Raised when the ML dataset temporal frontier is absent or invalid."""


def parse_required_ml_dataset_valid_from(config: Mapping[str, Any]) -> datetime:
    """Return the required ML dataset frontier as UTC-aware datetime."""
    raw = config.get("ml_dataset_valid_from") if config else None
    if raw in (None, ""):
        raise MLDatasetConfigError(
            "config_profiles.ml_config missing required key ml_dataset_valid_from"
        )

    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        value = raw.strip()
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"
        elif value.endswith("+00"):
            value = f"{value}:00"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise MLDatasetConfigError(
                f"invalid ml_dataset_valid_from value: {raw!r}"
            ) from exc
    else:
        raise MLDatasetConfigError(
            f"invalid ml_dataset_valid_from type: {type(raw).__name__}"
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
