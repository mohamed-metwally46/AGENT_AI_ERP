"""``python -m evaluation`` -- run the golden set and gate on the result.

The CI entry point, and deliberately a small one. It resolves the same
principals the API resolves, through the same authentication chain, so an
evaluation cannot accidentally run with a scope no real caller could obtain --
which would make every authorization measurement meaningless.

Exit codes are the contract:

``0``
    Every gate passed.
``1``
    A gate failed, or a case did not complete.
``2``
    The run could not start: bad configuration, unreachable database, malformed
    golden set. Distinguished from ``1`` so a broken pipeline is not read as a
    security regression, and a security regression is never read as a broken
    pipeline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from erp_agent.core.bootstrap import bootstrap
from erp_agent.core.container import Container
from erp_agent.core.event_loop import run as run_async
from erp_agent.domain.errors import ErpAgentError
from erp_agent.domain.models.security_context import SecurityContext
from evaluation.harness import LIVE, REPLAY, Harness
from evaluation.models import GoldenSetError, load_golden_set
from evaluation.report import Report, render_text

__all__ = ["main"]

_USAGE = """usage: python -m evaluation [--mode replay|live] [--golden DIR] [--json PATH]

Runs the golden set against a live database and reports Plan.md section 12's
metrics.

options:
  --mode      replay (default, deterministic, the CI gate) or live (calls the
              configured model; measures tool selection; not reproducible).
  --golden    Directory of golden files. Defaults to the packaged set.
  --json      Also write the machine-readable report here.
"""


async def _resolve_principals(container: Container) -> dict[str, SecurityContext]:
    """Resolve every dev credential to the context it grants.

    Through the real authentication chain rather than by constructing contexts,
    so a golden case runs as a scope some credential actually confers. Building
    one by hand here would let the suite measure a scope nobody can obtain.
    """
    import yaml

    from erp_agent.infrastructure.security.fixture_access_tokens import (
        default_token_fixture_path,
    )

    tokens = yaml.safe_load(default_token_fixture_path().read_text(encoding="utf-8"))

    contexts: dict[str, SecurityContext] = {}
    for row in tokens["tokens"]:
        caller = await container.authenticate(row["token"])
        contexts[caller.context.scope.value] = caller.context
    return contexts


async def _run(mode: str, golden_dir: Path | None) -> Report:
    """Boot, resolve principals, and run the golden set."""
    golden = load_golden_set(golden_dir)
    container = await bootstrap()
    try:
        harness = Harness(
            container=container,
            contexts=await _resolve_principals(container),
            mode=mode,
            live_llm=container.llm_client if mode == LIVE else None,
        )
        return await harness.run(golden)
    finally:
        await container.close()


def main(argv: list[str] | None = None, *, stream: TextIO | None = None) -> int:
    """Run the evaluation and return the exit code.

    Args:
        argv: Command-line arguments, excluding the program name.
        stream: Where to write the report. Defaults to standard output.

    Returns:
        ``0`` when every gate passed, ``1`` on a gate failure, ``2`` when the
        run could not start.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", choices=[REPLAY, LIVE], default=REPLAY)
    parser.add_argument("--golden", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--help", "-h", action="store_true")

    arguments = parser.parse_args(argv)
    out = stream if stream is not None else sys.stdout

    if arguments.help:
        out.write(_USAGE)
        return 0

    try:
        report = run_async(_run(arguments.mode, arguments.golden))
    except GoldenSetError as exc:
        out.write(f"golden set error\n\n  {exc}\n\n")
        return 2
    except ErpAgentError as exc:
        out.write(f"the evaluation could not start\n\n  {exc.message}\n\n")
        return 2

    out.write(render_text(report))

    if arguments.json is not None:
        arguments.json.write_text(report.as_json() + "\n", encoding="utf-8")
        out.write(f"  report      {arguments.json}\n\n")

    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
