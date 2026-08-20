"""Structured logging configuration.

Log events are structured data, not formatted strings. JSON anywhere real; a
readable console renderer locally, because developers read logs with their eyes.

Plan.md section 20 sets the boundary for what may be logged: the request, the
principal, the tool, *safe* arguments, the authorization decision, timings and a
result status. Never a credential, never a salary, never PII, never a raw
restricted row. Nothing in this module makes that automatic -- it is a rule the
call sites keep -- but the errors in :mod:`erp_agent.domain.errors` are built to
carry structured, non-sensitive context so that keeping it is the easy path.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog

from erp_agent.config import LogFormat, Settings

__all__ = ["configure_logging", "get_logger"]


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    """Configure structlog and the standard library logging module.

    Safe to call more than once; the last call wins.

    Args:
        settings: Supplies the level and the renderer to use.
        stream: Where rendered events are written. Defaults to standard error,
            so a diagnostics report on standard output stays machine-readable.
    """
    level = getattr(logging, settings.app.log_level, logging.INFO)
    destination = stream if stream is not None else sys.stderr

    logging.basicConfig(format="%(message)s", stream=destination, level=level, force=True)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.app.log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=destination),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a component name.

    Args:
        name: Component identifier, conventionally the module's ``__name__``.

    Returns:
        A structlog logger. Events are structured: pass fields as keyword
        arguments rather than formatting them into the message.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
