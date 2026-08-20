"""What one golden case produced, before any metric reads it.

Deliberately a record of *observations*, not of judgements. The harness fills
this in; the metric modules turn it into numbers. Keeping the two apart is what
lets a metric be tested against a hand-built observation with no database, no
model and no network -- which is what "metrics are reproducible" has to mean if
it is to mean anything.

``payloads`` holds what the model was shown. That is the material a grounding
check needs: a figure in an answer is grounded if it appeared in something the
model was given, and there is no way to ask that question without keeping the
thing it was given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from erp_agent.application.agent.trace import AgentTrace
from erp_agent.domain.policies.grounding import SourceKind, SourceReference
from evaluation.models import GoldenCase

__all__ = ["CaseOutcome", "CaseResult"]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Why a case did not produce an answer, when it did not."""

    error_code: str
    message: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Everything observed while running one case.

    Attributes:
        case: What was asked and what was expected.
        answer: The final answer text, after unresolvable markers were removed.
        sources: What the answer cited.
        stripped_markers: Markers the model invented. A citation-validity
            signal that costs nothing to collect, because the loop already
            computes it.
        trace: Every tool call, already sanitised.
        payloads: What the model was shown, in order. Never rendered into a
            report -- it contains real HR data -- but read by the grounding and
            leakage checks.
        control_row: What the case's control query returned, if it has one.
        failure: Set when the run raised instead of answering.
    """

    case: GoldenCase
    answer: str = ""
    sources: tuple[SourceReference, ...] = ()
    stripped_markers: tuple[int, ...] = ()
    trace: AgentTrace = field(default_factory=AgentTrace)
    payloads: tuple[str, ...] = ()
    control_row: dict[str, Any] | None = None
    failure: CaseOutcome | None = None

    @property
    def answered(self) -> bool:
        """Whether the run produced an answer at all."""
        return self.failure is None

    @property
    def tools_used(self) -> tuple[str, ...]:
        """Tools that actually ran, in order, without repeats."""
        return self.trace.tools_used

    @property
    def cited_documents(self) -> frozenset[str]:
        """Document ids the answer cited."""
        return frozenset(
            source.citation.document_id for source in self.sources if source.citation is not None
        )

    @property
    def cited_kinds(self) -> frozenset[SourceKind]:
        """Which kinds of source the answer cited."""
        return frozenset(source.kind for source in self.sources)

    @property
    def searchable_text(self) -> str:
        """Everything a leak could be hiding in, as one string.

        The answer, every payload the model saw, and the sanitised trace. A
        restricted value that reached any of them reached somewhere it should
        not have: the answer is what the user sees, the payloads are what the
        model saw, and the trace is what the operator's log will hold.
        """
        return "\n".join([self.answer, *self.payloads, str(self.trace.as_log_payload())])
