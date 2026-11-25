"""Event-driven, display-only Gate candle projection. No decision persistence."""
from copy import deepcopy
import math

from .mtf_chart_engine import TF_SECONDS
from .mtf_forming_preview import forming_preview


def gate_candle(message, symbol, timeframes, now):
    if message.get('channel') != 'spot.candlesticks' or message.get('event') != 'update':
        return None
    item = message.get('result')
    if not isinstance(item, dict):
        return None
    tf, _, pair = str(item.get('n', '')).partition('_')
    if pair != symbol or tf not in timeframes:
        return None
    try:
        candle = dict(time=int(item['t']), open=float(item['o']), high=float(item['h']),
                      low=float(item['l']), close=float(item['c']), volume=float(item['a']),
                      is_closed=item.get('w') is True)
        stamp = float(message.get('time_ms', float(message['time']) * 1000)) / 1000
        if not all(math.isfinite(v) for v in candle.values()) or not math.isfinite(stamp):
            return None
        if (candle['time'] % TF_SECONDS[tf] or candle['time'] > now or stamp > now + 1
                or candle['low'] <= 0 or candle['volume'] < 0
                or not candle['low'] <= min(candle['open'], candle['close'])
                <= max(candle['open'], candle['close']) <= candle['high']):
            return None
        return tf, candle, stamp
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


class LivePreview:
    def __init__(self, seed, cfg, *, families=None, windows=None):
        self.seed = deepcopy(seed)
        self.cfg = cfg
        self.raw = {tf: deepcopy(frame['candles']) for tf, frame in seed['frames'].items()}
        self.stamps = {}
        self.families = families
        self.windows = windows

    def update(self, message, now):
        return self.update_batch([message], now)

    def absorb(self, message, now):
        parsed = gate_candle(message, self.seed['symbol'], self.raw, now)
        if parsed is None:
            return None
        tf, candle, stamp = parsed
        # Never replay a queued/out-of-order tick as current market data.
        if now - stamp > self.cfg.poll_seconds * self.cfg.stale_tf_multiplier:
            return None
        key = (tf, candle['time'])
        if stamp <= self.stamps.get(key, -1):
            return None
        trigger = self.cfg.stage('trigger')
        previous = self.raw[tf][-1] if self.raw[tf] else None
        if not previous or candle['time'] < previous['time']:
            return None
        # Reseed from the canonical closed-candle path at every boundary.
        # Exchange w=true and preview predicates never create an official signal.
        trigger_open = self.raw[trigger][-1]['time']
        if now >= trigger_open + TF_SECONDS[trigger] or candle['time'] > previous['time'] or candle['is_closed']:
            return 'resync'
        if previous.get('is_closed') is True:
            return 'resync'
        self.stamps[key] = stamp
        self.raw[tf][-1] = candle
        return tf, candle, stamp

    def update_batch(self, messages, now):
        latest = None
        for message in messages:
            accepted = self.absorb(message, now)
            if accepted == 'resync':
                return {'type': 'resync'}
            if accepted:
                latest = accepted
        if latest is None:
            return None
        tf, candle, stamp = latest
        analysis = self.seed['analysis']
        preview = forming_preview(self.seed['symbol'], self.raw, self.seed['frames'], self.cfg,
                                  now, analysis.get('spread_pct'), analysis.get('btc_trend'),
                                  indicators=self.seed['indicator_config'], families=self.families,
                                  windows=self.windows, flow=analysis.get('pump_flow') if self.cfg.schema_version == 'mtf_chart_v3' else None)
        preview.update(delivery='stream', source_event_at=stamp)
        return dict(type='preview', symbol=self.seed['symbol'], config_hash=analysis['config_hash'],
                    profile_hash=analysis.get('profile_hash'), profile_id=analysis.get('profile_id'),
                    indicator_hash=(analysis.get('indicator_identity') or {}).get('config_hash'),
                    candles={key: {**rows[-1], 'closed': False} for key, rows in self.raw.items() if rows and not rows[-1].get('is_closed')},
                    preview=preview, timeframe=tf, candle={**candle, 'closed': False})
