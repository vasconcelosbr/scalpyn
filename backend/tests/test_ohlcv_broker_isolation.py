"""Real Redis + Celery workers, synthetic task bodies (never market/API traffic)."""
import os
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4
import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from kombu import Queue
from app.tasks.celery_app import TASK_ROUTES, QUEUE_CANONICAL_OHLCV, QUEUE_RESEARCH_OHLCV


def test_busy_research_worker_does_not_starve_canonical_collector():
    url = os.getenv('PR_A_TEST_REDIS_URL')
    if not url:
        pytest.skip('PR_A_TEST_REDIS_URL required for real broker test')
    parsed = urlparse(url)
    assert parsed.hostname in {'127.0.0.1','localhost'} and parsed.path == '/15'
    # A dedicated test Redis DB plus unique queue names protect other local work.
    suffix = uuid4().hex
    canonical, research = QUEUE_CANONICAL_OHLCV+'_'+suffix, QUEUE_RESEARCH_OHLCV+'_'+suffix
    routes = {name:{'queue':canonical if route['queue']==QUEUE_CANONICAL_OHLCV else research}
              for name,route in TASK_ROUTES.items() if route['queue'] in {QUEUE_CANONICAL_OHLCV,QUEUE_RESEARCH_OHLCV}}
    app = Celery('pr_a_'+suffix,broker=url,backend=url,set_as_current=False)
    app.conf.update(task_routes=routes,task_queues=(Queue(canonical),Queue(research),Queue('unconsumed_'+suffix)),
                    worker_prefetch_multiplier=1,task_default_queue='unconsumed_'+suffix,
                    task_create_missing_queues=False,broker_connection_retry_on_startup=False)
    entered, release = threading.Event(), threading.Event()
    research_name='app.tasks.collect_research_ohlcv.capture_state_comparison'
    canonical_name='app.tasks.collect_research_ohlcv.collect_1m_shadow'
    # Other tests may already have registered the real tasks through Celery's
    # shared finalizers. Finalize first, then replace only this test app's tasks.
    app.finalize()
    app.tasks.pop(research_name, None)
    app.tasks.pop(canonical_name, None)

    @app.task(name=research_name,shared=False,lazy=False)
    def heavy():
        entered.set()
        if not release.wait(30):
            raise RuntimeError('test release deadline exceeded')
        return 'research_finished'

    @app.task(name=canonical_name,shared=False,lazy=False)
    def capture():
        return 'canonical_persisted'

    try:
        with start_worker(app,queues=[research],pool='solo',concurrency=1,perform_ping_check=False,hostname='research-'+suffix):
            with start_worker(app,queues=[canonical],pool='solo',concurrency=1,perform_ping_check=False,hostname='canonical-'+suffix):
                job=heavy.apply_async()
                assert entered.wait(10), 'research worker did not start'
                collected=capture.apply_async()
                assert collected.get(timeout=10) == 'canonical_persisted'
                assert not job.ready(), 'research was not saturated during canonical execution'
                # A stale message is discarded, never executed after recovery.
                expired=heavy.apply_async(expires=datetime.now(timezone.utc)-timedelta(seconds=1))
                release.set()
                assert job.get(timeout=10) == 'research_finished'
                from celery.exceptions import TaskRevokedError
                with pytest.raises(TaskRevokedError):
                    expired.get(timeout=10)
                collected.forget(); job.forget(); expired.forget()
    finally:
        release.set()
        app.close()
