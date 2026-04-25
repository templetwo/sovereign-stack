"""Centralized logging configuration for sovereign_stack.

Use logger = get_logger(__name__) at module top, then logger.{debug, info,
warning, error, exception}. Level configurable via SOVEREIGN_LOG_LEVEL env.
"""
import logging
import os

_DEFAULT_LEVEL = "INFO"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        level = os.environ.get("SOVEREIGN_LOG_LEVEL", _DEFAULT_LEVEL).upper()
        logging.basicConfig(level=level, format=_FORMAT)
        _configured = True
    return logging.getLogger(name)
