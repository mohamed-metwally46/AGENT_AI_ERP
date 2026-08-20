"""Did the right documents come back, and did the answer cite them.

Precision and recall are computed over the documents the answer *cited*, not
over the chunks retrieval returned. That is a deliberate choice and it makes the
numbers slightly harsher than the textbook ones.

Retrieval that returns the right passage and an answer that never cites it are
not the same outcome, and only one of them is useful to a reader. Measuring the
retriever alone would report success for a run whose answer was unsupported;
measuring what was cited measures the thing the user actually gets. The cost is
that a model which retrieves well and cites badly scores as a retrieval failure,
which is worth saying out loud in a report rather than hiding in a definition.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rag.domain.policies import derive_document_id

from evaluation.results import CaseResult

__all__ = ["RetrievalMetrics", "cited_correctly", "precision", "recall", "retrieval_metrics"]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """Plan.md section 12's RAG group.

    Attributes:
        precision: Cited documents that were expected, over cited documents.
        recall: Expected documents that were cited, over expected documents.
        citation_validity: Fraction of answers where every marker resolved.
            The loop strips markers that do not, so this counts answers with no
            stripped markers.
        cases: How many cases contributed.
    """

    precision: float | None
    recall: float | None
    citation_validity: float | None
    cases: int


def expected_ids(result: CaseResult) -> frozenset[str]:
    """The document ids a case expects, derived from the filenames it names.

    Derived rather than written out, by RAG's own rule, so a golden file cannot
    drift from the index it is measuring.
    """
    return frozenset(derive_document_id(name) for name in result.case.expect_documents)


def precision(result: CaseResult) -> float | None:
    """Fraction of the answer's citations that were expected.

    Args:
        result: One case's observations.

    Returns:
        ``None`` when the answer cited nothing -- an uncited answer has no
        precision to speak of, and averaging a zero in would punish it twice,
        once here and once in recall.
    """
    cited = result.cited_documents
    if not cited:
        return None
    return len(cited & expected_ids(result)) / len(cited)


def recall(result: CaseResult) -> float | None:
    """Fraction of the expected documents the answer cited.

    Args:
        result: One case's observations.

    Returns:
        ``None`` for a case that expects no particular document.
    """
    expected = expected_ids(result)
    if not expected:
        return None
    return len(result.cited_documents & expected) / len(expected)


def cited_correctly(result: CaseResult) -> bool:
    """Whether every marker in the answer resolved to a real source.

    Plan.md section 12 wants 100%, and the mechanism that delivers it is in the
    loop: a marker that resolves to nothing is removed and recorded. So this
    measures whether the *model* invented one, which is the quality signal --
    the control itself is not in question.
    """
    return not result.stripped_markers


def retrieval_metrics(results: Sequence[CaseResult]) -> RetrievalMetrics:
    """Aggregate the RAG group over a run.

    Args:
        results: Every case's observations.

    Returns:
        The metrics, over the cases that name expected documents.
    """
    considered = [r for r in results if r.case.expect_documents]

    return RetrievalMetrics(
        precision=_mean([precision(r) for r in considered]),
        recall=_mean([recall(r) for r in considered]),
        citation_validity=_mean([float(cited_correctly(r)) for r in results if r.answered]),
        cases=len(considered),
    )


def _mean(values: Sequence[float | None]) -> float | None:
    """Mean of the values that exist, or ``None`` when none do."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)
