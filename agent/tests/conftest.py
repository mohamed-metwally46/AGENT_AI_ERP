"""Shared test configuration.

Two concerns live here. The event loop must be one psycopg can use (see
:mod:`erp_agent.core.event_loop`), and the tests that need a live database must
skip themselves rather than fail when it is absent -- a developer without Docker
running should still get a green unit suite.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import pytest

from erp_agent.core.event_loop import loop_factory

#: Read from the environment rather than from Settings so a broken configuration
#: still produces a skip rather than a collection error.
_DSN_VARIABLE = "ERP_DATABASE_URL"


@pytest.fixture(scope="session")
def _asyncio_loop_factory() -> Callable[[], asyncio.AbstractEventLoop]:
    """Build pytest-asyncio's loops the same way the application does.

    Overriding pytest-asyncio's own fixture is the supported way to choose a
    loop without an event loop *policy*: policies are deprecated from Python
    3.14, and this package treats warnings as errors, so the policy route fails
    outright rather than merely aging badly.

    Without this, every async test touching psycopg fails on Windows with
    "Psycopg cannot use the 'ProactorEventLoop'".
    """
    return loop_factory


@pytest.fixture(scope="session")
def database_url() -> str:
    """The DSN for the live mock HR database, or skip the test.

    Resolved the same way the application resolves it -- process environment
    first, then ``.env`` -- so a developer who configured one and not the other
    does not get a confusing skip. Wrapped in a ``try`` because a broken
    configuration should skip these tests, not fail collection for the whole
    suite.
    """
    if dsn := os.environ.get(_DSN_VARIABLE, "").strip():
        return dsn

    try:
        from erp_agent.config import Settings

        return Settings.load().database.dsn.get_secret_value()
    except Exception as exc:
        pytest.skip(f"no usable {_DSN_VARIABLE} ({exc}); the live database tests need one")
