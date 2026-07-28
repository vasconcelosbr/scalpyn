"""Pre-fit minimum detectable net-EV calculation."""

from __future__ import annotations

from math import sqrt
from statistics import NormalDist
from typing import Any, Sequence

import numpy as np

from ..data_contract import CanonicalObservation


def minimum_detectable_net_ev(
    observations: Sequence[CanonicalObservation],
    *,
    posterior_probability: float,
    practical_rope_pct: float,
) -> dict[str, Any]:
    """Return a conservative one-sided normal-approximation MDE.

    This is a pre-fit feasibility gate, not a replacement for the Bayesian
    posterior. The calculation uses only observed immutable net returns.
    """

    values = np.asarray(
        [
            item.net_pnl_pct
            for item in observations
            if item.net_pnl_pct is not None
            and np.isfinite(item.net_pnl_pct)
        ],
        dtype=float,
    )
    if values.size < 2:
        return {
            "status": "INSUFFICIENT",
            "n": int(values.size),
            "reason": "fewer_than_two_finite_net_returns",
        }
    observed_std = float(values.std(ddof=1))
    z_score = float(NormalDist().inv_cdf(posterior_probability))
    standard_error = observed_std / sqrt(int(values.size))
    detectable_lift = practical_rope_pct + z_score * standard_error
    return {
        "status": "CALCULATED",
        "method": "one_sided_normal_approximation",
        "n": int(values.size),
        "observed_net_return_std_pct": observed_std,
        "posterior_probability": posterior_probability,
        "z_score": z_score,
        "standard_error_pct": standard_error,
        "practical_rope_pct": practical_rope_pct,
        "minimum_detectable_net_ev_pct": detectable_lift,
    }
