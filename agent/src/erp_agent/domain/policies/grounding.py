"""Grounding: which sources an answer may cite, and what it must say when they disagree.

Phase 5's hybrid questions are the reason this exists. A structured question has
one source and a document question has one source; a hybrid question has two, and
two sources introduce three problems that neither single-source case has.

*Markers must mean one thing across a whole run.* A retrieval returns passages
numbered ``[1]``, ``[2]``, ``[3]``. A second retrieval in a later round returns
its own ``[1]``. If both reach the model, the ``[1]`` in the final answer resolves
to two different chunks and the citation is worthless. :class:`GroundingLedger`
assigns markers once, run-wide, and renumbers evidence as it arrives.

*A marker the model invents must not survive.* The model can emit ``[7]`` when
six sources exist. :meth:`GroundingLedger.ground` strips any marker that does not
resolve, which is Plan.md's R4 mitigation stated exactly: markers resolve only to
retrieved metadata, unknown markers are stripped. Stripping rather than erroring
because the surrounding sentence may still be sound; what must not happen is a
citation that looks resolvable and is not.

*Where the two sources disagree, the answer has to say so.* Not pick one. A
document that says five days' notice and a ``leave_policies`` row that says seven
are both real, and an answer that quietly reports one has hidden the interesting
fact. :func:`policy_discrepancies` finds the cases it can find and hands them to
the model to state.

**What the discrepancy check is, and is not.** It is a narrow comparison over a
fixed table of rules (:data:`COMPARABLE_RULES`), each anchored to cue wording
that denotes the same quantity as its column. It is not a semantic comparison of
prose against data, and it cannot be: "absences longer than 10 consecutive days
need department-head approval" and ``max_consecutive_days = 15`` are both true,
and a checker naive enough to compare those two numbers would manufacture a
conflict out of a threshold and a cap. That rule is deliberately absent from the
table for exactly that reason.

So silence here is not evidence that the sources agree -- only that no rule in
the table matched with differing values. The check exists to catch the
disagreements it can prove, and the prompt carries the general instruction for
the rest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from erp_agent.domain.models.hr import LeavePolicy

if TYPE_CHECKING:
    # Annotations only, and deliberately so. Importing these at runtime would
    # close a cycle: `domain.models.sql` imports the read-only policy, which
    # loads this package, which would then be importing `domain.ports` before
    # `domain.models` had finished. Nothing here needs the classes themselves --
    # `dataclasses.replace` works on any instance -- so the names stay types.
    from erp_agent.domain.ports.knowledge import Citation, DocumentEvidence, RetrievedPassage

__all__ = [
    "COMPARABLE_RULES",
    "NOT_FOUND_PHRASE",
    "ComparableRule",
    "GroundedAnswer",
    "GroundingLedger",
    "PolicyDiscrepancy",
    "SourceKind",
    "SourceReference",
    "cites_both_sources",
    "policy_discrepancies",
    "render_discrepancies",
]

#: What the model is told to say when neither source supports an answer. A fixed
#: phrase rather than a paraphrase, so Phase 8 can count it (Plan.md section 9.2:
#: "an explicit 'not found in the indexed documents', never a guess").
NOT_FOUND_PHRASE = "I could not find that in the documents or records available to you."

#: Matches a citation marker in model output.
_MARKER = re.compile(r"\[(\d+)\]")

#: Tidies the punctuation left behind when a marker is removed mid-sentence.
_ORPHANED_SPACE = re.compile(r" +([,.;:)])")
_DOUBLE_SPACE = re.compile(r"[ \t]{2,}")


class SourceKind(StrEnum):
    """Where a cited fact came from.

    The distinction Plan.md section 12 measures ("both sources cited when both
    used"), so it is a type rather than a convention over tool names.
    """

    STRUCTURED = "structured"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class SourceReference:
    """One numbered source the answer is allowed to cite.

    Attributes:
        marker: The run-wide ``[n]``. Unique across everything the model saw
            while answering one question, which is the property a per-retrieval
            numbering does not have.
        kind: Structured record or document passage.
        tool_name: Which tool produced it.
        label: How the source reads to a person.
        citation: Provenance, for document sources. ``None`` for structured
            ones, whose provenance is the tool name and the arguments already
            recorded in the trace.
        row_count: How many records a structured source returned.
    """

    marker: int
    kind: SourceKind
    tool_name: str
    label: str
    citation: Citation | None = None
    row_count: int | None = None

    def __post_init__(self) -> None:
        """Validate that the reference can actually be resolved.

        Raises:
            ValueError: If the marker is not one-based, the tool is unnamed, or
                the provenance does not match the kind -- a document source
                without a citation would be a marker pointing at nothing.
        """
        if self.marker < 1:
            raise ValueError("citation markers are one-based")
        if not self.tool_name.strip():
            raise ValueError("a source must name the tool that produced it")
        if self.kind is SourceKind.DOCUMENT and self.citation is None:
            raise ValueError("a document source must carry its citation")
        if self.kind is SourceKind.STRUCTURED and self.citation is not None:
            raise ValueError("a structured source has no document citation")


@dataclass(frozen=True, slots=True)
class ComparableRule:
    """One quantity that exists in both a policy document and a policy row.

    Attributes:
        key: Stable identifier, used in the trace and in tests.
        description: How the rule is named to the model.
        field: The :class:`~erp_agent.domain.models.hr.LeavePolicy` attribute.
        unit: ``days`` or ``months``.
        cues: Phrases that must appear in the same sentence as the number for it
            to count as this rule. Tight on purpose: a loose cue turns an
            unrelated figure into a fabricated conflict.
    """

    key: str
    description: str
    field: str
    unit: str
    cues: tuple[str, ...]


#: The rules whose prose wording denotes the same quantity as the column.
#:
#: Deliberately short. Every entry here is one where a document sentence and a
#: ``leave_policies`` column are talking about the same number, so a difference
#: between them is a real disagreement rather than two compatible facts.
COMPARABLE_RULES: tuple[ComparableRule, ...] = (
    ComparableRule(
        key="annual_entitlement",
        description="annual entitlement",
        field="annual_entitlement_days",
        unit="days",
        cues=("entitlement", "per fiscal year", "per year", "annually"),
    ),
    ComparableRule(
        key="carry_forward",
        description="days that may be carried into the next year",
        field="max_carry_forward_days",
        unit="days",
        cues=("carried", "carry forward", "carry-forward", "carried forward"),
    ),
    ComparableRule(
        key="notice",
        description="notice required before leave starts",
        field="notice_days",
        unit="days",
        cues=("notice", "before the intended start", "before the start"),
    ),
    ComparableRule(
        key="minimum_service",
        description="service required before the policy applies",
        field="min_service_months",
        unit="months",
        cues=("of service",),
    ),
)


@dataclass(frozen=True, slots=True)
class PolicyDiscrepancy:
    """A quantity the document and the database state differently.

    Attributes:
        rule: Which rule disagrees.
        document_value: What the passage says.
        structured_value: What the policy row says.
        document_marker: The passage's run-wide marker.
        structured_marker: The policy result's run-wide marker.
    """

    rule: ComparableRule
    document_value: Decimal
    structured_value: Decimal
    document_marker: int
    structured_marker: int

    def describe(self) -> str:
        """State the disagreement in one sentence, taking neither side."""
        return (
            f"{self.rule.description}: the document says "
            f"{_plain(self.document_value)} {self.rule.unit} [{self.document_marker}], "
            f"the HR records say {_plain(self.structured_value)} {self.rule.unit} "
            f"[{self.structured_marker}]"
        )


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    """A final answer with its citations resolved.

    Attributes:
        text: The answer, with any unresolvable marker removed.
        sources: The sources actually cited, in marker order.
        stripped_markers: Markers that resolved to nothing and were removed.
            Recorded rather than discarded: a model that keeps inventing markers
            is a quality signal Phase 8 should be able to measure.
    """

    text: str
    sources: tuple[SourceReference, ...] = ()
    stripped_markers: tuple[int, ...] = ()

    @property
    def kinds_cited(self) -> frozenset[SourceKind]:
        """Which kinds of source the answer actually cited."""
        return frozenset(source.kind for source in self.sources)

    @property
    def cites_both_sources(self) -> bool:
        """Whether the answer cited a record *and* a document."""
        return cites_both_sources(self.sources)

    @property
    def citations(self) -> tuple[Citation, ...]:
        """The document citations among the cited sources."""
        return tuple(s.citation for s in self.sources if s.citation is not None)


@dataclass(frozen=True, slots=True)
class GroundingLedger:
    """Every source seen while answering one question, numbered once.

    Immutable, and threaded through the loop the way ``AgentTrace`` is: each
    recording returns a new ledger. A mutable accumulator would be shorter and
    would also make "which sources existed at the moment this was rendered" a
    question with no answer.

    Attributes:
        references: The numbered sources, in the order they arrived.
        passages: Document passages, already renumbered. Kept because the
            discrepancy check reads their text.
        policies: Entitlement rules seen so far, with the marker of the result
            they came from.
    """

    references: tuple[SourceReference, ...] = ()
    passages: tuple[RetrievedPassage, ...] = ()
    policies: tuple[tuple[int, LeavePolicy], ...] = ()

    @property
    def next_marker(self) -> int:
        """The marker the next source will be given."""
        return len(self.references) + 1

    def with_document(
        self,
        evidence: DocumentEvidence,
        *,
        tool_name: str,
    ) -> tuple[GroundingLedger, DocumentEvidence]:
        """Record retrieved passages under run-wide markers.

        Args:
            evidence: What the search returned, numbered from one.
            tool_name: The tool that produced it.

        Returns:
            ``(ledger, evidence)`` where the evidence has been renumbered. The
            renumbered copy is what the model is shown, so the markers it sees
            are the markers the answer will be validated against.
        """
        marker = self.next_marker
        renumbered = tuple(
            replace(passage, citation=replace(passage.citation, marker=marker + offset))
            for offset, passage in enumerate(evidence.passages)
        )
        references = tuple(
            SourceReference(
                marker=passage.citation.marker,
                kind=SourceKind.DOCUMENT,
                tool_name=tool_name,
                label=passage.citation.label,
                citation=passage.citation,
            )
            for passage in renumbered
        )

        return (
            GroundingLedger(
                references=(*self.references, *references),
                passages=(*self.passages, *renumbered),
                policies=self.policies,
            ),
            replace(evidence, passages=renumbered),
        )

    def with_structured(
        self,
        result: object,
        *,
        tool_name: str,
        label: str,
    ) -> tuple[GroundingLedger, SourceReference]:
        """Record one structured tool result as a single citable source.

        One marker per result rather than per row: a caller cites "the leave
        balances this tool returned", and numbering forty rows individually
        would produce markers nobody uses and a context nobody reads.

        Args:
            result: What the tool returned.
            tool_name: The tool that produced it.
            label: How the source reads to a person.

        Returns:
            ``(ledger, reference)``.
        """
        rows = result if isinstance(result, tuple | list) else None
        reference = SourceReference(
            marker=self.next_marker,
            kind=SourceKind.STRUCTURED,
            tool_name=tool_name,
            label=label,
            row_count=None if rows is None else len(rows),
        )
        policies = tuple(
            (reference.marker, row) for row in (rows or ()) if isinstance(row, LeavePolicy)
        )

        return (
            GroundingLedger(
                references=(*self.references, reference),
                passages=self.passages,
                policies=(*self.policies, *policies),
            ),
            reference,
        )

    def discrepancies(self) -> tuple[PolicyDiscrepancy, ...]:
        """Every disagreement the rule table can prove from what is recorded.

        Deduplicated on the substance of the disagreement -- which rule, which
        two values, which passage -- and not on which result carried the figure.
        Asking for the same policy twice is a reasonable thing for a model to
        do, and it does not create a second conflict.
        """
        if not self.passages or not self.policies:
            return ()

        seen: set[tuple[str, Decimal, Decimal, int]] = set()
        found: list[PolicyDiscrepancy] = []
        for marker, policy in self.policies:
            for discrepancy in policy_discrepancies(
                policy, self.passages, structured_marker=marker
            ):
                identity = (
                    discrepancy.rule.key,
                    discrepancy.document_value,
                    discrepancy.structured_value,
                    discrepancy.document_marker,
                )
                if identity not in seen:
                    seen.add(identity)
                    found.append(discrepancy)
        return tuple(found)

    def ground(self, text: str) -> GroundedAnswer:
        """Resolve the markers in a final answer.

        Args:
            text: What the model produced.

        Returns:
            The answer with unresolvable markers removed, and the sources it
            genuinely cited.
        """
        known = {reference.marker: reference for reference in self.references}
        cited: set[int] = set()
        stripped: list[int] = []

        def resolve(match: re.Match[str]) -> str:
            marker = int(match.group(1))
            if marker in known:
                cited.add(marker)
                return match.group(0)
            stripped.append(marker)
            return ""

        rewritten = _MARKER.sub(resolve, text)
        if stripped:
            rewritten = _tidy(rewritten)

        return GroundedAnswer(
            text=rewritten,
            sources=tuple(known[marker] for marker in sorted(cited)),
            stripped_markers=tuple(sorted(set(stripped))),
        )


def cites_both_sources(sources: Iterable[SourceReference]) -> bool:
    """Whether these sources include both a record and a document.

    Plan.md section 12's hybrid metric ("both sources cited when both used"),
    computed rather than eyeballed. A function rather than a method on one of
    the two types that need it, so the answer the loop returns and the grounded
    answer the policy produces cannot drift apart on what "both" means.

    Args:
        sources: The sources an answer cited.

    Returns:
        ``True`` only when both kinds are present.
    """
    return frozenset(source.kind for source in sources) == {
        SourceKind.STRUCTURED,
        SourceKind.DOCUMENT,
    }


def policy_discrepancies(
    policy: LeavePolicy,
    passages: Sequence[RetrievedPassage],
    *,
    structured_marker: int,
) -> tuple[PolicyDiscrepancy, ...]:
    """Compare one entitlement rule against what the passages say.

    Only organisation-wide policies are compared. A grade-specific row and a
    handbook paragraph are not describing the same thing -- the handbook states
    the standard and defers to the grade band -- so comparing them would report a
    conflict where the two documents agree by design.

    Args:
        policy: The structured rule.
        passages: Document passages retrieved in the same run.
        structured_marker: The marker the policy result was given.

    Returns:
        One discrepancy per rule where both sources state a value and the values
        differ. Empty when they agree, and empty when no cue matched -- which is
        not the same as agreement; see the module docstring.
    """
    if policy.scope != "all":
        return ()

    found: list[PolicyDiscrepancy] = []
    for rule in COMPARABLE_RULES:
        structured = _as_decimal(getattr(policy, rule.field, None))
        if structured is None:
            continue
        for passage in passages:
            stated = _stated_value(passage.text, rule)
            if stated is not None and stated != structured:
                found.append(
                    PolicyDiscrepancy(
                        rule=rule,
                        document_value=stated,
                        structured_value=structured,
                        document_marker=passage.marker,
                        structured_marker=structured_marker,
                    )
                )
                break

    return tuple(found)


def render_discrepancies(discrepancies: Iterable[PolicyDiscrepancy]) -> str:
    """Tell the model that two sources it has been given disagree.

    Args:
        discrepancies: What was found.

    Returns:
        A note for the tool result, or an empty string when there is nothing to
        report.
    """
    found = list(discrepancies)
    if not found:
        return ""

    lines = [
        "SOURCES DISAGREE. The document and the HR records state different "
        "values for the same rule. Say so explicitly in your answer, give both "
        "figures with their markers, and do not silently choose one:",
    ]
    lines.extend(f"- {discrepancy.describe()}" for discrepancy in found)
    return "\n".join(lines)


def _stated_value(text: str, rule: ComparableRule) -> Decimal | None:
    """Find the value a passage states for one rule, if it states one clearly.

    Requires a cue phrase and the number in the same sentence, and the number to
    carry the rule's own unit. All three, because any two of them match plenty of
    sentences that mean something else. One adjective is tolerated between the
    two -- policy prose says "21 working days" and "5 unused days" -- and the cue
    is what keeps that tolerance from turning any nearby number into a rule.
    """
    pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s+(?:[a-z]+\s+)?" + rule.unit[:-1] + r"s?\b",
        re.IGNORECASE,
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n{2,}", text):
        lowered = sentence.lower()
        if not any(cue in lowered for cue in rule.cues):
            continue
        match = pattern.search(sentence)
        if match is not None:
            return Decimal(match.group(1))
    return None


def _as_decimal(value: object) -> Decimal | None:
    """Normalise a policy field to a comparable number."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return None


def _plain(value: Decimal) -> str:
    """Render a number without the trailing zeros a ``NUMERIC`` column carries."""
    normalised = value.normalize()
    return f"{normalised:f}"


def _tidy(text: str) -> str:
    """Repair the spacing a removed marker leaves behind."""
    return _DOUBLE_SPACE.sub(" ", _ORPHANED_SPACE.sub(r"\1", text)).strip()
