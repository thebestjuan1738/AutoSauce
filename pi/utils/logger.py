"""
logger.py - Centralized logging. Import `log` everywhere.

Usage:
    from pi.utils.logger import log
    log.info("Gantry at home")
    log.error("Gripper stall detected")
"""

import collections
import logging
import sys
import time

# Holds the last 500 log records for the /api/logs endpoint
_LOG_BUFFER: collections.deque = collections.deque(maxlen=500)


class _InMemoryHandler(logging.Handler):
    """Appends each record to the shared in-memory buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        _LOG_BUFFER.append({
            "time":    time.strftime("%H:%M:%S", time.localtime(record.created)),
            "level":   record.levelname,
            "message": record.getMessage(),
        })


def get_recent_logs() -> list:
    """Return a copy of the in-memory log buffer (newest last)."""
    return list(_LOG_BUFFER)


def _build() -> logging.Logger:
    logger = logging.getLogger("saucebot")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s", "%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    mh = _InMemoryHandler()
    mh.setFormatter(fmt)
    logger.addHandler(mh)

    return logger

log = _build()