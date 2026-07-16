"""Backlog guard (2026-07-16) — expires em tasks periódicas idempotentes.

Uma relíquia de 143k+88k mensagens (~227MB Redis) foi observada: tasks de alta
frequência acumulam no broker durante downtime de worker. O guard injeta
``options.expires = max(3*intervalo, 60)`` em todo schedule NUMÉRICO, limitando
o backlog. Crontab (baixa frequência) e chains ficam de fora.
"""
from celery.schedules import crontab

from app.tasks.celery_app import celery_app


def test_numeric_scheduled_tasks_have_expires():
    sched = celery_app.conf.beat_schedule
    checked = 0
    for name, entry in sched.items():
        interval = entry.get("schedule")
        if isinstance(interval, (int, float)) and not isinstance(interval, bool):
            opts = entry.get("options") or {}
            assert "expires" in opts, f"{name} sem expires"
            assert opts["expires"] == max(float(interval) * 3.0, 60.0), name
            checked += 1
    assert checked > 0


def test_crontab_tasks_not_forced_to_expire():
    sched = celery_app.conf.beat_schedule
    for name, entry in sched.items():
        if isinstance(entry.get("schedule"), crontab):
            opts = entry.get("options") or {}
            assert "expires" not in opts, f"{name} (crontab) não deveria ter expires forçado"


def test_high_frequency_monitor_capped():
    # trade_monitor (10s) e collect (60s) — os que empilharam — têm expires curto.
    sched = celery_app.conf.beat_schedule
    for name, entry in sched.items():
        if entry.get("task") == "app.tasks.trade_monitor.monitor":
            assert entry["options"]["expires"] == 60.0  # max(30, 60)
        if entry.get("task") == "app.tasks.collect_market_data.collect_all" and isinstance(
            entry.get("schedule"), (int, float)
        ):
            assert entry["options"]["expires"] == max(float(entry["schedule"]) * 3.0, 60.0)
