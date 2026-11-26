"""Restricted recovery schedule with an explicit M5/Shadow collection opt-in."""
import os

ALLOWED = (
    'fetch_market_caps', 'collect_research_15m_after_close',
    'collect_research_1h_after_close', 'pump_monitor_cycle',
    'mtf_chart_after_close',
    'mtf_history_after_close', 'mtf_history_maintenance', 'mtf_replay_progress',
    'mtf_pump_flow_revision',
)

M5_SHADOW_ALLOWED = (
    'collect_state_1m_every_30s', 'collect_state_5m_every_60s',
    'collect_5m_data_every_5min', 'pipeline_scan', 'shadow_trade_monitor',
)


def observation_schedule(schedule, environ=None):
    """RECOVERY_M5_SHADOW_SCHEDULE: never enable execution or ML schedules."""
    environ = os.environ if environ is None else environ
    allowed = ALLOWED
    if environ.get('RECOVERY_M5_SHADOW_ENABLED') == '1':
        if environ.get('RECOVERY_OBSERVATION_ONLY') != '1':
            raise RuntimeError('M5 Shadow recovery requires RECOVERY_OBSERVATION_ONLY=1')
        allowed += M5_SHADOW_ALLOWED
    return {name: schedule[name] for name in allowed}


if __name__ == '__main__':
    from app.tasks.celery_app import celery_app
    celery_app.conf.beat_schedule = observation_schedule(celery_app.conf.beat_schedule)
    print('Observation schedule:', ','.join(celery_app.conf.beat_schedule), flush=True)
    celery_app.start(['beat', '--loglevel=INFO', '--schedule=/tmp/celerybeat-schedule'])
