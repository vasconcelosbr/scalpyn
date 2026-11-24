"""Candle provenance travels beside scalar values, never as a scoring feature."""
from datetime import datetime, timezone
import pandas as pd


class IndicatorResults(dict):
    def __init__(self):
        super().__init__()
        self.key_metadata = {}


def closed_frame(df, timeframe, as_of=None):
    minutes = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440}
    if timeframe not in minutes or 'time' not in df:
        return df, None
    from .price_position import _closed_candles
    cutoff = as_of or datetime.now(timezone.utc)
    frame = _closed_candles(df, timeframe_minutes=minutes[timeframe], as_of=cutoff)
    if frame.empty:
        return frame, None
    frame['time'] = frame['_timestamp']
    opened = frame['_timestamp'].iloc[-1]
    return frame, {
        'timeframe': timeframe, 'candle_open_time': opened.isoformat(),
        'candle_close_time': (opened + pd.Timedelta(minutes=minutes[timeframe])).isoformat(),
        'source_timestamp': (opened + pd.Timedelta(minutes=minutes[timeframe])).isoformat(),
        'source_timestamp_semantics': 'CANDLE_CLOSE_TIME', 'candle_closed': True,
        'candle_policy': 'CLOSED_ONLY', 'temporal_contract': 'coherent_closed_candle_v1',
        'base_close': float(frame['close'].iloc[-1]), 'sample_count': len(frame),
    }
