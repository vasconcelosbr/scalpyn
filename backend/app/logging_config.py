"""Display-only log timestamp conversion to America/Sao_Paulo (GMT-3).

Storage stays UTC everywhere (DB columns, Celery scheduling/ETA computation
via ``celery_app.conf.timezone``); this only changes the wall-clock string
each log line prints, so operators reading logs don't have to mentally
convert from UTC.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

_LOGGER_NAMES = ("", "uvicorn", "uvicorn.access", "uvicorn.error")


class BrazilTimeFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone(BRAZIL_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(sep=" ", timespec="milliseconds")


def install_brazil_time_logging() -> None:
    """Swap every handler's formatter on the given loggers to BrazilTimeFormatter,
    preserving each handler's existing format string (only the time source changes)."""
    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            existing = handler.formatter
            fmt = existing._fmt if existing is not None else None
            datefmt = existing.datefmt if existing is not None else None
            handler.setFormatter(BrazilTimeFormatter(fmt, datefmt))
