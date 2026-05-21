"""Shared indicator name constants.

Single source of truth for indicator key names that are referenced across
multiple modules.  Import from here instead of defining local string literals
so that a rename only requires one change.

Design note — why three different "critical" sets exist
--------------------------------------------------------
``REQUIRED_CORE_INDICATORS``  (this file, also used by indicators_provider.py)
    The MINIMUM set that the *structural scheduler* must have written before any
    decision engine can proceed.  These are RSI/ADX/MACD — the three fields that
    would leave a decision engine completely blind if absent.

``indicator_validator.CRITICAL_INDICATORS``
    Guards the *IndicatorEnvelope* layer.  Includes ``volume_24h_usdt`` (a
    microstructure field) because the envelope validator runs on the full merged
    snapshot, not just the structural group.  Intentionally does NOT include
    ``macd_histogram`` — the envelope validator predates the histogram rename.

``robust_indicators.validation.CRITICAL_INDICATORS``
    Guards the *robust_indicators* scoring pipeline.  Includes ``ema50``
    (used in regime detection) in addition to the core three.

All three sets must include ``rsi`` and ``adx``.  ``macd_histogram`` (not
``macd``) is the canonical MACD key for decision logic — see the docstring
in ``indicators_provider.py`` for the full rationale.
"""

# Keys that the structural scheduler writes and that decision engines require.
# Order is stable — do not rearrange without updating all consumers.
# Renaming any key requires updating (in order):
#   1. feature_engine._calc_macd / _calc_rsi / _calc_adx  (writer side)
#   2. structural_scheduler_service                        (cadence/payload shape)
#   3. indicator_validity._PLAUSIBILITY_RULES              (validity rules)
#   4. all decision engines                                (consumer side)
REQUIRED_CORE_INDICATORS: tuple[str, ...] = ("adx", "rsi", "macd_histogram")
