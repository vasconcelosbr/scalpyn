import logging
from datetime import datetime, timezone

from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.logging_config import install_brazil_time_logging


def test_brazil_time_preserves_uvicorn_access_fields():
    logger = logging.getLogger("uvicorn.access")
    old = logger.handlers[:]
    handler = logging.StreamHandler()
    original = AccessFormatter("%(asctime)s %(levelprefix)s %(client_addr)s %(request_line)s %(status_code)s", use_colors=False)
    handler.setFormatter(original)
    logger.handlers = [handler]
    try:
        install_brazil_time_logging()
        install_brazil_time_logging()
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1", "GET", "/api/ml/catboost/readiness", "1.1", 200), None)
        record.created = datetime(2026, 9, 16, 12, tzinfo=timezone.utc).timestamp()
        rendered = handler.format(record)
        assert "2026-09-16 09:00:00.000-03:00" in rendered
        assert "GET /api/ml/catboost/readiness HTTP/1.1" in rendered
        assert "200 OK" in rendered
        assert isinstance(handler.formatter, AccessFormatter)
        assert handler.formatter is not original
    finally:
        logger.handlers = old


def test_brazil_time_preserves_uvicorn_default_prefix():
    logger = logging.getLogger("uvicorn.error")
    old = logger.handlers[:]
    handler = logging.StreamHandler()
    handler.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False))
    logger.handlers = [handler]
    try:
        install_brazil_time_logging()
        record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "Ready", (), None)
        assert handler.format(record).strip() == "INFO:     Ready"
    finally:
        logger.handlers = old
