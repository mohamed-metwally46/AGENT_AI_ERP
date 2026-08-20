"""Did the agent call the right tools, with the right arguments, and get the right answer.

Three measurements, and they are independent in a way worth keeping straight.

*Tool selection* is the model's judgement. It is only measurable in ``live``
mode -- in ``replay`` the calls are written down, so scoring them scores the
golden file. :func:`tool_selection` returns ``None`` there rather than 1.0,
because a metric that reports a perfect score for a thing it did not measure is
worse than a missing one.

*Parameter extraction* is also the model's, and also unmeasurable in replay for
the same reason. What *is* measurable in both modes is whether the arguments
that reached a tool were the ones the case expected -- which is what catches an
argument being renamed, dropped, or quietly coerced somewhere below the model.

*Query correctness* is entirely ours. A control query, written by a person and
run through the same read-only barrier, computes what the tool should have
returned. Plan.md section 12 wants 100% and means it: a headcount that is off by
one is a wrong answer delivered confidently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from evaluation.results import CaseResult

__all__ = [
    "ToolMetrics",
    "arguments_match",
    "query_correctness",
    "tool_metrics",
    "tool_selection",
]


@dataclass(frozen=True, slots=True)
class ToolMetrics:
    """Plan.md section 12's structured group.

    Attributes:
        selection_accuracy: Fraction of cases whose used tools were exactly the
            expected ones. ``None`` when not measurable -- see the module
            docstring.
        parameter_accuracy: Fraction of cases whose tool arguments matched.
            ``None`` in replay mode.
        argument_fidelity: Fraction of cases where the arguments that reached a
            tool were the expected ones. Measurable in both modes, because it
            is about the layers below the model.
        query_correctness: Fraction of cases whose tool result matched an
            independent control query.
        cases: How many cases contributed.
    """

    selection_accuracy: float | None
    parameter_accuracy: float | None
    argument_fidelity: float | None
    query_correctness: float | None
    cases: int


def tool_selection(result: CaseResult) -> bool:
    """Whether the tools that ran are exactly the ones expected.

    Args:
        result: One case's observations.

    Returns:
        ``True`` when the sets match. Order-insensitive: a correct answer that
        reads the policy before the balance is not wrong for doing so.
    """
    return set(result.tools_used) == set(result.case.expect_tools)


def arguments_match(result: CaseResult) -> bool:
    """Whether every expected argument reached its tool with the expected value.

    Only the arguments a case names are checked. A case that pins
    ``department`` says nothing about ``employment_status``, and should not
    fail when a model supplies one.

    Args:
        result: One case's observations.

    Returns:
        ``True`` when every named argument matched.
    """
    expected = result.case.expect_arguments
    if not expected:
        return True

    seen: dict[str, dict[str, Any]] = {}
    for invocation in result.trace.invocations:
        seen.setdefault(invocation.tool_name, {}).update(invocation.arguments)

    for tool, arguments in expected.items():
        actual = seen.get(tool)
        if actual is None:
            return False
        for name, value in arguments.items():
            if not _same(actual.get(name), value):
                return False
    return True


def query_correctness(result: CaseResult) -> bool | None:
    """Whether the tool agreed with the case's independent control query.

    Two-sided, and both sides are needed.

    *The control query must produce what the case says it produces.* Otherwise a
    control query whose meaning drifted -- a join changed, a filter dropped --
    would quietly start agreeing with a tool that drifted the same way, and the
    check would pass while both were wrong.

    *The figure must have reached the model.* The tool's result is compared as
    the model saw it, in the rendered payload, so the check covers the whole
    path rather than stopping at the value the tool returned. A correct query
    whose result is mangled on the way out is still a wrong answer.

    Args:
        result: One case's observations.

    Returns:
        ``True`` or ``False`` for a case with a control query, ``None`` for one
        without -- which keeps a case that does not check this from counting
        towards a metric it did not contribute to.
    """
    case = result.case
    if case.control_sql is None:
        return None
    if result.control_row is None:
        return False

    control_agrees = all(
        _same(result.control_row.get(name), value) for name, value in case.expect_row.items()
    )
    shown = "\n".join(result.payloads)
    tool_agrees = all(_appears(shown, value) for value in case.expect_row.values())

    return control_agrees and tool_agrees


def _appears(payload: str, value: object) -> bool:
    """Whether a control value is present in what the model was shown.

    Numbers are matched on their normalised form, because a ``NUMERIC`` column
    renders as ``"21.00"`` through the tool and is written ``21`` in a golden
    file, and a check that called those different would fail every leave case.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return str(value).lower() in payload.lower()
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
        candidates = {str(number), str(number.normalize()), f"{number:f}"}
        if number == number.to_integral_value():
            candidates.add(str(int(number)))
        return any(candidate in payload for candidate in candidates)
    return str(value).lower() in payload.lower()


def tool_metrics(results: Sequence[CaseResult], *, selection_measurable: bool) -> ToolMetrics:
    """Aggregate the structured group over a run.

    Args:
        results: Every case's observations.
        selection_measurable: Whether the model actually chose the tools. False
            in replay mode.

    Returns:
        The metrics. A group with no contributing cases reports ``None`` rather
        than ``1.0``: nothing measured is not everything correct.
    """
    considered = [r for r in results if r.case.expect_tools or r.case.expect_arguments]
    controlled = [(r, query_correctness(r)) for r in results]
    with_control = [verdict for _, verdict in controlled if verdict is not None]

    selection = _mean([tool_selection(r) for r in considered]) if selection_measurable else None
    parameters = _mean([arguments_match(r) for r in considered]) if selection_measurable else None

    return ToolMetrics(
        selection_accuracy=selection,
        parameter_accuracy=parameters,
        argument_fidelity=_mean([arguments_match(r) for r in considered]),
        query_correctness=_mean(with_control),
        cases=len(considered),
    )


def _mean(verdicts: Sequence[bool]) -> float | None:
    """Fraction true, or ``None`` when nothing was measured."""
    if not verdicts:
        return None
    return sum(1 for verdict in verdicts if verdict) / len(verdicts)


def _same(actual: object, expected: object) -> bool:
    """Compare a tool argument or a query cell against an expectation.

    Numbers are compared numerically, because YAML gives ``12`` as an ``int``
    and PostgreSQL gives ``NUMERIC`` as a ``Decimal``, and a metric that called
    those different would report a false regression on every run.
    """
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected
    if isinstance(actual, int | float | Decimal) and isinstance(expected, int | float | Decimal):
        return Decimal(str(actual)) == Decimal(str(expected))
    if actual is None or expected is None:
        return actual is expected
    return str(actual).strip().lower() == str(expected).strip().lower()
