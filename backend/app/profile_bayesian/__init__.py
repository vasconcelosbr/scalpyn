"""Profile Bayesian Intelligence.

This package is analytical only. It has no imports from ``app.ml`` and no
authority to mutate active profiles, score trades, execute orders, train, or
promote ML models.
"""

from .config import OperationalAuthority, feature_flags

__all__ = ["OperationalAuthority", "feature_flags"]
