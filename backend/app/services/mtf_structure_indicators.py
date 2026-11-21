"""Versioned display inputs added only after the observer's evaluation."""
from math import isfinite

import pandas as pd

from .feature_engine import FeatureEngine


STRUCTURE_INDICATOR_VERSION = 'mtf_structure_indicators_v1'
STRUCTURE_EMA_PERIOD = 50
STRUCTURE_EMA_NAME = f'ema{STRUCTURE_EMA_PERIOD}'
STRUCTURE_REQUIRED_INDICATORS = (STRUCTURE_EMA_NAME, 'atr')
STRUCTURE_INDICATOR_CONFIG = {'ema': {'enabled': True, 'periods': [STRUCTURE_EMA_PERIOD]}}


def enrich_observer_frames(data):
    """Reuse the producer EMA and window without changing any evaluation input.

    ATR already belongs to the engine: never replace or fabricate it here.
    Keep existing EMA samples and the original full-window warmup requirement.
    Only the latest closed sample is needed by the immutable snapshot.
    """
    for frame in data['frames'].values():
        if frame.get('indicators', {}).get(STRUCTURE_EMA_NAME):
            continue
        candles = [c for c in frame.get('candles', []) if c.get('closed')]
        window = frame.get('indicator_sample_count')
        if not window or window < STRUCTURE_EMA_PERIOD or len(candles) < window:
            continue
        value = FeatureEngine(STRUCTURE_INDICATOR_CONFIG)._calc_ema(
            pd.DataFrame(candles[-window:])).get(STRUCTURE_EMA_NAME)
        if value is None or not isfinite(value):
            continue
        frame.setdefault('indicators', {})[STRUCTURE_EMA_NAME] = [
            {'time': candles[-1]['time'], 'value': value}]
        frame.setdefault('display_only_indicators', {})[STRUCTURE_EMA_NAME] = {
            'version': STRUCTURE_INDICATOR_VERSION,
            'configuration': STRUCTURE_INDICATOR_CONFIG,
            'sample_count': window,
        }
