"""The grounding policy: run-wide markers, invented markers, and disagreements.

Three claims are under test, and they are separable.

*A marker means one thing per run.* Retrieval numbers its own passages from one.
Two retrievals in one question therefore both produce a ``[1]``, and if both
reach the model the answer's ``[1]`` resolves to two chunks. The ledger has to
renumber, and the renumbered evidence has to be what the model is shown.

*A marker the model invented does not survive.* Plan.md R4 says unknown markers
are stripped; these check that stripping happens, that it takes the marker and
not the sentence, and that the fabrication is recorded rather than swallowed.

*A disagreement between prose and a policy row is reported.* Including the case
that must **not** be reported: a document threshold and a database cap are two
compatible facts, and a checker that called them a conflict would be inventing
one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from erp_agent.domain.models.hr import LeavePolicy
from erp_agent.domain.policies.grounding import (
    COMPARABLE_RULES,
    NOT_FOUND_PHRASE,
    GroundingLedger,
    SourceKind,
    SourceReference,
    policy_discrepancies,
    render_discrepancies,
)
from erp_agent.domain.ports.knowledge import Citation, DocumentEvidence, RetrievedPassage

pytestmark = [pytest.mark.unit]

#: The wording of the real fixture document, which is what the check must handle.
ENTITLEMENT_PROSE = (
    "All permanent employees accrue annual leave from their date of joining. "
    "The standard entitlement is 21 working days per fiscal year."
)
NOTICE_PROSE = (
    "Requests must be submitted at least 5 working days before the intended "
    "start date. Absences longer than 10 consecutive working days require "
    "approval from the department head in addition to the line manager."
)
SERVICE_PROSE = (
    "Employees may not take annual leave during their first 3 months of "
    "service except in circumstances covered by the Emergency Leave Policy."
)
CARRY_PROSE = "Up to 5 unused days may be carried into the following fiscal year."


def _passage(text: str, *, marker: int = 1, title: str = "Annual Leave Policy") -> RetrievedPassage:
    return RetrievedPassage(
        text=text,
        score=0.9,
        citation=Citation(
            marker=marker,
            document_id="47a9adb9952e647654ade17ec397b664",
            document_title=title,
            chunk_id=f"chunk-{marker}",
        ),
    )


def _evidence(*texts: str, query: str = "leave policy") -> DocumentEvidence:
    return DocumentEvidence(
        query=query,
        passages=tuple(_passage(text, marker=n) for n, text in enumerate(texts, start=1)),
        searched_document_count=3,
    )


def _policy(**overrides: object) -> LeavePolicy:
    defaults: dict[str, object] = {
        "leave_code": "ANN",
        "leave_name": "Annual",
        "scope": "all",
        "annual_entitlement_days": Decimal(21),
    }
    return LeavePolicy(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestMarkersAreRunWide:
    def test_a_second_search_does_not_reuse_the_first_search_markers(self):
        ledger = GroundingLedger()
        ledger, first = ledger.with_document(_evidence("a", "b"), tool_name="search_documents")
        ledger, second = ledger.with_document(_evidence("c"), tool_name="search_documents")

        assert [p.marker for p in first.passages] == [1, 2]
        assert [p.marker for p in second.passages] == [3]

    def test_the_renumbered_evidence_is_what_carries_the_new_markers(self):
        # The model is shown the returned copy, so the numbers it sees are the
        # numbers the answer is validated against. If renumbering happened only
        # inside the ledger, the two would drift.
        ledger = GroundingLedger()
        ledger, _ = ledger.with_document(_evidence("a"), tool_name="search_documents")
        ledger, second = ledger.with_document(_evidence("b"), tool_name="search_documents")

        assert "[2]" in second.render_for_model()
        assert "[1]" not in second.render_for_model()

    def test_structured_and_document_sources_share_one_numbering(self):
        ledger = GroundingLedger()
        ledger, reference = ledger.with_structured(
            (_policy(),), tool_name="get_leave_policy", label="get_leave_policy (HR records)"
        )
        ledger, evidence = ledger.with_document(_evidence("a"), tool_name="search_documents")

        assert reference.marker == 1
        assert [p.marker for p in evidence.passages] == [2]

    def test_a_structured_result_is_one_source_not_one_per_row(self):
        ledger = GroundingLedger()
        ledger, reference = ledger.with_structured(
            (_policy(), _policy(scope="grade")),
            tool_name="get_leave_policy",
            label="policies",
        )

        assert reference.row_count == 2
        assert len(ledger.references) == 1

    def test_every_passage_becomes_a_citable_source(self):
        ledger = GroundingLedger()
        ledger, _ = ledger.with_document(_evidence("a", "b", "c"), tool_name="search_documents")

        assert [r.marker for r in ledger.references] == [1, 2, 3]
        assert all(r.kind is SourceKind.DOCUMENT for r in ledger.references)


class TestGroundingAnAnswer:
    def _ledger(self) -> GroundingLedger:
        ledger = GroundingLedger()
        ledger, _ = ledger.with_structured(
            (_policy(),), tool_name="get_leave_policy", label="policies"
        )
        ledger, _ = ledger.with_document(_evidence("a", "b"), tool_name="search_documents")
        return ledger

    def test_a_known_marker_survives_and_is_reported_as_cited(self):
        grounded = self._ledger().ground("You have enough leave [1], per the policy [2].")

        assert "[1]" in grounded.text
        assert "[2]" in grounded.text
        assert [s.marker for s in grounded.sources] == [1, 2]

    def test_an_invented_marker_is_stripped(self):
        grounded = self._ledger().ground("The rule is clear [9].")

        assert "[9]" not in grounded.text
        assert grounded.stripped_markers == (9,)

    def test_stripping_takes_the_marker_and_leaves_the_sentence(self):
        grounded = self._ledger().ground("Employees accrue 21 days a year [9].")

        assert grounded.text == "Employees accrue 21 days a year."

    def test_an_uncited_source_is_not_reported_as_cited(self):
        # What the answer stands on, not what happened to be available.
        grounded = self._ledger().ground("You have enough leave [1].")

        assert [s.marker for s in grounded.sources] == [1]

    def test_citing_both_kinds_is_detected(self):
        grounded = self._ledger().ground("Balance [1] against the policy [2].")

        assert grounded.cites_both_sources
        assert grounded.kinds_cited == {SourceKind.STRUCTURED, SourceKind.DOCUMENT}

    def test_citing_one_kind_is_not_reported_as_both(self):
        grounded = self._ledger().ground("The policy says so [2].")

        assert not grounded.cites_both_sources

    def test_only_document_sources_yield_citations(self):
        grounded = self._ledger().ground("Balance [1], policy [2].")

        assert [c.marker for c in grounded.citations] == [2]

    def test_an_answer_with_no_markers_is_left_alone(self):
        grounded = self._ledger().ground("Hello. How can I help?")

        assert grounded.text == "Hello. How can I help?"
        assert grounded.sources == ()
        assert grounded.stripped_markers == ()

    def test_an_empty_ledger_strips_every_marker(self):
        # Nothing was retrieved, so nothing is citable, so no marker resolves.
        grounded = GroundingLedger().ground("As documented [1].")

        assert "[1]" not in grounded.text
        assert grounded.stripped_markers == (1,)


class TestDisagreementsAreSurfaced:
    def test_a_differing_notice_period_is_reported(self):
        # The fixture document says five days; the fixture database says seven.
        found = policy_discrepancies(
            _policy(notice_days=7), [_passage(NOTICE_PROSE)], structured_marker=2
        )

        assert [d.rule.key for d in found] == ["notice"]
        assert found[0].document_value == Decimal(5)
        assert found[0].structured_value == Decimal(7)

    def test_a_differing_minimum_service_is_reported(self):
        found = policy_discrepancies(
            _policy(min_service_months=6), [_passage(SERVICE_PROSE)], structured_marker=2
        )

        assert [d.rule.key for d in found] == ["minimum_service"]
        assert found[0].document_value == Decimal(3)

    def test_agreement_is_not_reported_as_a_conflict(self):
        found = policy_discrepancies(
            _policy(annual_entitlement_days=Decimal(21), max_carry_forward_days=Decimal(5)),
            [_passage(ENTITLEMENT_PROSE), _passage(CARRY_PROSE, marker=2)],
            structured_marker=3,
        )

        assert found == ()

    def test_a_threshold_and_a_cap_are_not_a_conflict(self):
        # "absences longer than 10 consecutive days need extra approval" and
        # `max_consecutive_days = 15` are both true. A checker that compared
        # them would manufacture a conflict, so no rule in the table matches
        # that wording.
        found = policy_discrepancies(
            _policy(max_consecutive_days=15), [_passage(NOTICE_PROSE)], structured_marker=2
        )

        assert all(d.rule.key != "max_consecutive" for d in found)
        assert "max_consecutive" not in {rule.key for rule in COMPARABLE_RULES}

    def test_a_grade_specific_policy_is_never_compared_to_the_handbook(self):
        # The handbook states the standard and defers to the grade band, so
        # comparing a grade row against it reports a conflict where the two
        # agree by design.
        found = policy_discrepancies(
            _policy(scope="grade", annual_entitlement_days=Decimal(30)),
            [_passage(ENTITLEMENT_PROSE)],
            structured_marker=2,
        )

        assert found == ()

    def test_a_rule_the_document_does_not_state_is_not_a_conflict(self):
        found = policy_discrepancies(
            _policy(notice_days=7),
            [_passage("Leave accrues monthly in arrears.")],
            structured_marker=2,
        )

        assert found == ()

    def test_the_note_names_both_figures_and_neither_wins(self):
        found = policy_discrepancies(
            _policy(notice_days=7), [_passage(NOTICE_PROSE)], structured_marker=2
        )

        note = render_discrepancies(found)

        assert "SOURCES DISAGREE" in note
        assert "5 days" in note
        assert "7 days" in note
        assert "do not silently choose one" in note

    def test_nothing_found_renders_nothing(self):
        assert render_discrepancies(()) == ""

    def test_the_ledger_finds_them_across_rounds(self):
        # The point of assembling both sources: the policy arrived in one round
        # and the passage in another, and the conflict is only visible together.
        ledger = GroundingLedger()
        ledger, _ = ledger.with_structured(
            (_policy(notice_days=7),), tool_name="get_leave_policy", label="policies"
        )
        assert ledger.discrepancies() == ()

        ledger, _ = ledger.with_document(_evidence(NOTICE_PROSE), tool_name="search_documents")

        assert [d.rule.key for d in ledger.discrepancies()] == ["notice"]

    def test_asking_twice_does_not_report_the_conflict_twice(self):
        ledger = GroundingLedger()
        ledger, _ = ledger.with_document(_evidence(NOTICE_PROSE), tool_name="search_documents")
        ledger, _ = ledger.with_structured(
            (_policy(notice_days=7),), tool_name="get_leave_policy", label="policies"
        )
        ledger, _ = ledger.with_structured(
            (_policy(notice_days=7),), tool_name="get_leave_policy", label="policies"
        )

        assert len(ledger.discrepancies()) == 1


class TestSourceReferenceRefusesToBeUnresolvable:
    def test_a_document_source_must_carry_a_citation(self):
        with pytest.raises(ValueError, match="must carry its citation"):
            SourceReference(marker=1, kind=SourceKind.DOCUMENT, tool_name="t", label="l")

    def test_a_structured_source_must_not_pretend_to_have_one(self):
        with pytest.raises(ValueError, match="no document citation"):
            SourceReference(
                marker=1,
                kind=SourceKind.STRUCTURED,
                tool_name="t",
                label="l",
                citation=_passage("x").citation,
            )

    def test_markers_are_one_based(self):
        with pytest.raises(ValueError, match="one-based"):
            SourceReference(marker=0, kind=SourceKind.STRUCTURED, tool_name="t", label="l")

    def test_a_source_must_name_its_tool(self):
        with pytest.raises(ValueError, match="name the tool"):
            SourceReference(marker=1, kind=SourceKind.STRUCTURED, tool_name="  ", label="l")


class TestTheNotFoundPhrase:
    def test_it_is_a_fixed_phrase_so_it_can_be_counted(self):
        # Plan.md section 9.2 wants an explicit not-found answer, and Phase 8
        # has to be able to detect it without matching a paraphrase.
        assert NOT_FOUND_PHRASE
        assert NOT_FOUND_PHRASE.strip() == NOT_FOUND_PHRASE
