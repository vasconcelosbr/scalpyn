"""Real Redis + process-isolated Celery workers; no market/API traffic."""
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from celery import Celery
from celery.exceptions import TaskRevokedError
from celery.signals import worker_ready
from kombu import Queue
from app.tasks.celery_app import TASK_ROUTES, QUEUE_CANONICAL_OHLCV, QUEUE_RESEARCH_OHLCV

RESEARCH_TASK = 'app.tasks.collect_research_ohlcv.capture_state_comparison'
CANONICAL_TASK = 'app.tasks.collect_research_ohlcv.collect_1m_shadow'


def _test_app(url, suffix):
    parsed = urlparse(url)
    assert parsed.hostname in {'127.0.0.1','localhost'} and parsed.path == '/15'
    # A dedicated test Redis DB plus unique queue names protect other local work.
    canonical, research = QUEUE_CANONICAL_OHLCV+'_'+suffix, QUEUE_RESEARCH_OHLCV+'_'+suffix
    routes = {name:{'queue':canonical if route['queue']==QUEUE_CANONICAL_OHLCV else research}
              for name,route in TASK_ROUTES.items() if route['queue'] in {QUEUE_CANONICAL_OHLCV,QUEUE_RESEARCH_OHLCV}}
    app = Celery('pr_a_'+suffix,broker=url,backend=url,set_as_current=False)
    app.conf.update(task_routes=routes,task_queues=(Queue(canonical),Queue(research),Queue('unconsumed_'+suffix)),
                    worker_prefetch_multiplier=1,task_default_queue='unconsumed_'+suffix,
                    task_create_missing_queues=False,broker_connection_retry_on_startup=False)
    return app


def _worker_main(url, suffix, queue, hostname, entered, release, ready):
    """Spawn without inheriting Celery registries or Kombu's global event loop.

    Embedded workers in threads share Kombu's process-global hub on Linux.
    Blocking a solo task can then stall the other embedded worker; Windows
    disables that event loop and masks the invalid test topology.
    """
    app = _test_app(url, suffix)
    app.finalize()
    app.tasks.pop(RESEARCH_TASK, None)
    app.tasks.pop(CANONICAL_TASK, None)

    @app.task(name=RESEARCH_TASK,shared=False,lazy=False)
    def heavy():
        entered.set()
        if not release.wait(30):
            raise RuntimeError('test release deadline exceeded')
        return {'status': 'research_finished', 'pid': os.getpid()}

    @app.task(name=CANONICAL_TASK,shared=False,lazy=False)
    def capture():
        return {'status': 'canonical_persisted', 'pid': os.getpid()}

    def on_ready(**_kwargs):
        ready.set()

    worker_ready.connect(on_ready, weak=False)
    try:
        app.Worker(queues=[queue], pool='solo', concurrency=1, hostname=hostname,
                   without_gossip=True, without_mingle=True, without_heartbeat=True,
                   loglevel='WARNING').start()
    finally:
        worker_ready.disconnect(on_ready)
        app.close()


def test_busy_research_worker_does_not_starve_canonical_collector():
    url = os.getenv('PR_A_TEST_REDIS_URL')
    if not url:
        pytest.skip('PR_A_TEST_REDIS_URL required for real broker test')
    suffix = uuid4().hex
    app = _test_app(url, suffix)
    context = multiprocessing.get_context('spawn')
    entered, release = context.Event(), context.Event()
    workers, hostnames, results, forced_shutdowns = [], [], [], []
    try:
        readiness = []
        for queue in (QUEUE_RESEARCH_OHLCV, QUEUE_CANONICAL_OHLCV):
            hostname = queue+'_'+suffix+'@localhost'
            ready = context.Event()
            process = context.Process(target=_worker_main,
                args=(url, suffix, queue+'_'+suffix, hostname, entered, release, ready))
            process.start()
            workers.append(process)
            hostnames.append(hostname)
            readiness.append(ready)
        for process, ready in zip(workers, readiness):
            assert ready.wait(30), f'worker {process.pid} did not become ready (exit={process.exitcode})'
        job = app.send_task(RESEARCH_TASK)
        results.append(job)
        assert entered.wait(10), 'research worker did not start'
        collected = app.send_task(CANONICAL_TASK)
        results.append(collected)
        assert collected.get(timeout=10) == {'status': 'canonical_persisted', 'pid': workers[1].pid}
        assert len({workers[0].pid, workers[1].pid, os.getpid()}) == 3
        assert not job.ready(), 'research was not saturated during canonical execution'
        # A stale message is discarded, never executed after recovery.
        expired = app.send_task(RESEARCH_TASK, expires=datetime.now(timezone.utc)-timedelta(seconds=1))
        results.append(expired)
        release.set()
        assert job.get(timeout=10) == {'status': 'research_finished', 'pid': workers[0].pid}
        with pytest.raises(TaskRevokedError):
            expired.get(timeout=10)
    finally:
        # Unblock the task BEFORE joining workers, including on assertion failures.
        release.set()
        with suppress(Exception):
            app.control.broadcast('shutdown', destination=hostnames)
        for process in workers:
            process.join(10)
            if process.is_alive():
                forced_shutdowns.append(process.pid)
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
        for result in results:
            with suppress(Exception):
                result.forget()
        app.close()
    assert not forced_shutdowns, f'workers required forced shutdown: {forced_shutdowns}'
    assert all(process.exitcode == 0 for process in workers)
