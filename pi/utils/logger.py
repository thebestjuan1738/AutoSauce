"""
logger.py — Centralized logging. Import `log` everywhere.

Usage:
    from pi.utils.logger import log
    log.info("Gantry at home")
    log.error("Gripper stall detected")
"""

import logging
import sys

def _build() -> logging.Logger:
    logger = logging.getLogger("saucebot")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s", "%H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

log = _build()
