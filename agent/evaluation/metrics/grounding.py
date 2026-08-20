"""Is every claim in the answer traceable to something the model was given.

**What this measures, and what it does not.** A general check for "claims not in
context" is a judgement, and the honest way to make it is another model -- which
is neither deterministic nor free, and would put a language model in the CI gate
that decides whether a language model regressed.

So this measures the sharpest checkable subset: **every number in the answer
appeared in something the model was shown.** For an ERP that is the claim that
matters. A prose flourish nobody can verify is a style problem; "you have 14
days of leave" when the tool said 4 is a wrong answer someone will act on, and
it is exactly the failure a fluent model produces most readily.

The limitation is real and stated rather than buried: a fabricated *sentence*
containing no figure passes this check. Phase 8 measures what it can measure
deterministically and says so in the report, instead of reporting a
hallucination rate it did not compute.

Two adjustments keep the signal honest rather than merely high:

*Numbers that came from the question are excluded.* "Is 5 days enough?" puts a 5
in the answer that came from the user, not from a tool, and counting it as
ungrounded would flag every well-formed answer.

*Citation markers are not numbers.* ``[1]`` is a reference, and stripping the
markers before the scan is what stops every cited answer scoring as ungrounded.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from erp_agent.domain.policies.grounding import SourceKind
from evaluation.results import CaseResult

__all__ = [
    "GroundingMetrics",
    "both_sources_cited",
    "grounding_metrics",
    "ungrounded_figures",
]

#: A citation marker, removed before figures are counted.
_MARKER = re.compile(r"\[\d+\]")

#: A number as a reader would write one, with optional thousands separators and
#: decimals. Matched on word boundaries so an identifier fragment is not a
#: figure.
_FIGURE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w.])")

#: Figures nobody needs a tool for. Ordinals and small counts appear in phrasing
#: ("the first of the two", "both") often enough that flagging them would drown
#: the real signal, and none of them is an ERP figure.
_UNREMARKABLE = frozenset({"0", "1", "2"})


@dataclass(frozen=True, slots=True)
class GroundingMetrics:
    """Plan.md section 12's grounding and hybrid metrics.

    Attributes:
        grounded_answers: Fraction of answers whose every figure appeared in
            something the model was shown.
        ungrounded_figure_count: How many distinct figures were not.
        both_sources_when_used: Fraction of hybrid cases that cited a record
            *and* a document.
        cases: How many answers contributed.
    """

    grounded_answers: float | None
    ungrounded_figure_count: int
    both_sources_when_used: float | None
    cases: int


def _figures(text: str) -> set[str]:
    """Every figure a reader would take as a claim."""
    stripped = _MARKER.sub(" ", text)
    return {
        match.group(1).replace(",", "")
        for match in _FIGURE.finditer(stripped)
        if match.group(1).replace(",", "") not in _UNREMARKABLE
    }


def ungrounded_figures(result: CaseResult) -> frozenset[str]:
    """Figures in the answer that appeared nowhere the model could have read.

    Args:
        result: One case's observations.

    Returns:
        The unsupported figures. Empty is the expected outcome.
    """
    if not result.answered:
        return frozenset()

    from_question = _figures(result.case.question)
    available = "\n".join(result.payloads).replace(",", "")

    return frozenset(
        figure for figure in _figures(result.answer) - from_question if figure not in available
    )


def both_sources_cited(result: CaseResult) -> bool:
    """Whether the answer cited a record and a document."""
    return result.cited_kinds == {SourceKind.STRUCTURED, SourceKind.DOCUMENT}


def grounding_metrics(results: Sequence[CaseResult]) -> GroundingMetrics:
    """Aggregate grounding over a run.

    Args:
        results: Every case's observations.

    Returns:
        The metrics.
    """
    answered = [r for r in results if r.answered]
    hybrid = [r for r in results if r.case.expect_both_sources]
    ungrounded = [ungrounded_figures(r) for r in answered]

    return GroundingMetrics(
        grounded_answers=_mean([not figures for figures in ungrounded]),
        ungrounded_figure_count=sum(len(figures) for figures in ungrounded),
        both_sources_when_used=_mean([both_sources_cited(r) for r in hybrid]),
        cases=len(answered),
    )


def _mean(verdicts: Sequence[bool]) -> float | None:
    """Fraction true, or ``None`` when nothing was measured."""
    if not verdicts:
        return None
    return sum(1 for verdict in verdicts if verdict) / len(verdicts)
