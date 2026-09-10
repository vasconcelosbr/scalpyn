import logging

from app.logging_config import BrazilTimeFormatter, install_brazil_time_logging


def test_formats_utc_epoch_as_gmt_minus_3():
    record = logging.LogRecord("test", logging.INFO, "x.py", 1, "hello", None, None)
    record.created = 1757534400.0  # 2025-09-10 20:00:00 UTC
    formatted = BrazilTimeFormatter("%(asctime)s %(message)s").format(record)
    assert formatted.startswith("2025-09-10 17:00:00")
    assert formatted.rstrip().endswith("hello")


def test_offset_is_fixed_minus_three_no_dst():
    record = logging.LogRecord("test", logging.INFO, "x.py", 1, "m", None, None)
    record.created = 1757534400.0
    assert BrazilTimeFormatter().formatTime(record) == "2025-09-10 17:00:00.000-03:00"


def test_install_preserves_existing_format_and_datefmt():
    root = logging.getLogger("")
    saved_handlers = list(root.handlers)
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(handler)
    try:
        install_brazil_time_logging()
        assert isinstance(handler.formatter, BrazilTimeFormatter)
        record = logging.LogRecord("t", logging.INFO, "x.py", 1, "m", None, None)
        record.created = 1757534400.0
        assert handler.formatter.format(record) == "17:00:00 | m"
    finally:
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
