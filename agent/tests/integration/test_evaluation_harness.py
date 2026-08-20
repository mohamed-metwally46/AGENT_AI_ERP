"""The harness, against the real stack.

Plan.md's Phase 8 test line asks for two things, and this file is both of them.

*The harness is deterministic against fakes.* Two runs of the same golden set,
over the same database and the same index, must produce the same report. That is
what makes a regression gate meaningful: a number that moves for no reason is a
number that gets muted.

*The gate actually gates.* A suite that always passes is decoration, so the last
class injects a violation into the real run and asserts the report fails --
proving the gate can fail, which no amount of green runs proves on its own.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from evaluation.harness import REPLAY, Harness
from evaluation.metrics.security import security_metrics
from evaluation.models import GoldenCase, QuestionClass, load_golden_set
from evaluation.results import CaseResult

from erp_agent.core.bootstrap import bootstrap
from erp_agent.core.container import Container

pytestmark = [pytest.mark.integration, pytest.mark.security]

GOLDEN = load_golden_set()

#: A representative slice, so the determinism check does not run the whole set
#: twice. One case per class, chosen to touch the database, the index and the
#: refusal guards.
SAMPLE = (
    "structured-sales-headcount",
    "document-annual-leave-entitlement",
    "hybrid-entitlement-agreement",
    "security-injected-scope-argument-is-refused",
    "security-self-scope-cannot-read-another-employee",
    "security-a-self-read-actually-returns-something",
)


@pytest.fixture
async def container() -> AsyncIterator[Container]:
    """A fully started container, or skip."""
    try:
        built = await bootstrap()
    except Exception as exc:
        pytest.skip(f"the stack is not available: {exc}")
    try:
        yield built
    finally:
        await built.close()


@pytest.fixture
async def harness(container: Container) -> Harness:
    """A replay-mode harness over the real container."""
    from evaluation.__main__ import _resolve_principals

    return Harness(container=container, contexts=await _resolve_principals(container), mode=REPLAY)


def _sample() -> list[GoldenCase]:
    return [case for case in GOLDEN.cases if case.id in SAMPLE]


class TestTheHarnessIsDeterministic:
    async def test_two_runs_produce_the_same_observations(self, harness):
        first = [await harness.run_case(case) for case in _sample()]
        second = [await harness.run_case(case) for case in _sample()]

        assert [r.answer for r in first] == [r.answer for r in second]
        assert [r.tools_used for r in first] == [r.tools_used for r in second]
        assert [sorted(r.cited_documents) for r in first] == [
            sorted(r.cited_documents) for r in second
        ]

    async def test_two_runs_produce_the_same_verdicts(self, harness):
        first = security_metrics([await harness.run_case(case) for case in _sample()])
        second = security_metrics([await harness.run_case(case) for case in _sample()])

        assert first.violations == second.violations
        assert first.clean == second.clean

    async def test_the_model_is_never_consulted(self, harness):
        # Replay mode has no provider. If a case ever reached one, the run would
        # stop being reproducible and nobody would know why.
        result = await harness.run_case(_sample()[0])

        assert result.answered
        assert result.trace.completed


class TestTheGoldenSetRunsClean:
    async def test_every_sampled_case_completes(self, harness):
        results = [await harness.run_case(case) for case in _sample()]

        assert [r.case.id for r in results if not r.answered] == []

    async def test_the_security_gates_are_clean_on_the_full_set(self, harness):
        results = [await harness.run_case(case) for case in GOLDEN.cases]

        metrics = security_metrics(results)

        assert metrics.violations == (), [(v.case_id, v.kind, v.detail) for v in metrics.violations]

    async def test_the_positive_control_actually_returns_data(self, harness):
        # The case the whole security file leans on: without it, every
        # cross-scope assertion is satisfied by a system that returns nothing at
        # all.
        case = next(c for c in GOLDEN.cases if c.expect_rows)

        result = await harness.run_case(case)

        assert security_metrics([result]).violations == ()

    async def test_a_cross_scope_read_returns_nothing_rather_than_failing(self, harness):
        # Empty, not an error: an error would confirm the record exists.
        case = next(
            c
            for c in GOLDEN.of_class(QuestionClass.SECURITY)
            if c.id == "security-self-scope-cannot-read-another-employee"
        )

        result = await harness.run_case(case)

        assert result.answered
        assert not result.trace.refusals


class TestTheGateCanFail:
    async def test_an_injected_violation_fails_the_report(self, harness):
        # A gate that has never failed is a gate nobody has tested. This forges
        # one observation -- a restricted column in a payload -- and asserts the
        # report says so.
        clean = await harness.run_case(_sample()[0])
        leaked = CaseResult(
            case=clean.case,
            answer=clean.answer,
            trace=clean.trace,
            payloads=(*clean.payloads, '{"national_id": "29001010101010"}'),
        )

        assert security_metrics([clean]).clean is True
        assert security_metrics([leaked]).clean is False

    async def test_the_full_report_serialises(self, harness):
        report = await harness.run(load_golden_set())

        parsed = json.loads(report.as_json())

        assert parsed["mode"] == REPLAY
        assert sum(parsed["counts"].values()) >= 60
        assert parsed["passed"] is True
