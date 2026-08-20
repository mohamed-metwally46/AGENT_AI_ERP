"""The composition root: where ports meet the adapters that implement them."""

from __future__ import annotations

from erp_agent.core.bootstrap import (
    bootstrap,
    load_settings,
    run_startup_checks,
    verify_write_barrier,
)
from erp_agent.core.container import Container
from erp_agent.core.event_loop import loop_factory, run
from erp_agent.core.logging import configure_logging, get_logger

__all__ = [
    "Container",
    "bootstrap",
    "configure_logging",
    "get_logger",
    "load_settings",
    "loop_factory",
    "run",
    "run_startup_checks",
    "verify_write_barrier",
]
