"""Choosing an event loop psycopg can actually use.

On Windows, Python's default event loop is ``ProactorEventLoop``, and psycopg's
async mode refuses to run on it -- it needs a selector-based loop to wait on the
libpq socket. The failure is loud but arrives at connect time, from inside the
pool, as "the database could not be reached", which is a misleading thing for a
platform incompatibility to look like.

So the choice is made once, here, and every entry point runs through
:func:`run`. On POSIX this is the ordinary default loop and the function is a
thin wrapper; on Windows it is the difference between working and not.

``asyncio.Runner`` is used rather than an event loop *policy* because policies
are deprecated from Python 3.14 and this package treats warnings as errors.
"""

from __future__ import annotations

import asyncio
import os
import selectors
from collections.abc import Coroutine
from typing import Any

__all__ = ["loop_factory", "run"]

# `os.name` rather than `sys.platform`: mypy narrows `sys.platform` to the
# platform it is running on, which makes the other branch statically
# unreachable and fails `warn_unreachable`. The choice must stay a runtime one --
# this code is checked on one platform and runs on others.
_IS_WINDOWS = os.name == "nt"


def loop_factory() -> asyncio.AbstractEventLoop:
    """Create an event loop the database driver can use.

    Returns:
        A selector-based loop on Windows; the platform default elsewhere.
    """
    if _IS_WINDOWS:
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion on a driver-compatible event loop.

    Args:
        coroutine: The coroutine to run.

    Returns:
        Whatever the coroutine returned.
    """
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(coroutine)
