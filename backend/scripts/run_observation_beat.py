"""Restricted recovery schedule plus the isolated MTF chart observer.

Preserves the production observation-only allowlist. No trading, pipeline,
training, promotion, or Shadow Portfolio execution schedules are enabled.
"""
from app.tasks.celery_app import celery_app

ALLOWED = (
    'fetch_market_caps', 'collect_research_15m_after_close',
    'collect_research_1h_after_close', 'pump_monitor_cycle',
    'mtf_chart_after_close',
)

if __name__ == '__main__':
    schedule = celery_app.conf.beat_schedule
    celery_app.conf.beat_schedule = {name:schedule[name] for name in ALLOWED}
    print('Observation schedule:', ','.join(ALLOWED), flush=True)
    celery_app.start(['beat', '--loglevel=INFO', '--schedule=/tmp/celerybeat-schedule'])
