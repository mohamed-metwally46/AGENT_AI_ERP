"""The golden set as data: what it must contain, and what it refuses.

Two jobs. The first is that the packaged set actually meets Plan.md section 11's
Phase 8 acceptance criterion 1 -- a suite that quietly shrank below its stated
coverage would keep passing while measuring less.

The second is that a malformed case fails loudly. A golden file is the thing
every metric is computed against, so an expectation that was silently dropped
turns the whole run into a number nobody can interpret. Every refusal below is a
way a case could have looked fine and measured nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evaluation.models import GoldenSetError, QuestionClass, load_golden_set

pytestmark = [pytest.mark.unit]

GOLDEN = load_golden_set()


class TestCoverage:
    def test_there_are_at_least_sixty_cases(self):
        assert len(GOLDEN) >= 60

    @pytest.mark.parametrize(
        ("question_class", "minimum"),
        [
            (QuestionClass.DOCUMENT, 15),
            (QuestionClass.STRUCTURED, 20),
            (QuestionClass.HYBRID, 15),
            (QuestionClass.SECURITY, 10),
        ],
    )
    def test_each_class_meets_its_floor(self, question_class, minimum):
        assert len(GOLDEN.of_class(question_class)) >= minimum

    def test_case_ids_are_unique(self):
        ids = [case.id for case in GOLDEN.cases]

        assert len(ids) == len(set(ids))

    def test_the_order_is_stable(self):
        # A report whose rows reorder between runs cannot be diffed, and a gate
        # that cannot diff is a gate that gets ignored.
        assert [case.id for case in load_golden_set().cases] == [case.id for case in GOLDEN.cases]

    def test_every_case_names_a_principal_the_dev_fixture_has(self):
        import yaml

        from erp_agent.infrastructure.security.fixture_principal_resolver import (
            default_fixture_path,
        )

        principals = yaml.safe_load(default_fixture_path().read_text(encoding="utf-8"))
        scopes = {row["scope"] for row in principals["principals"]}

        assert {case.principal for case in GOLDEN.cases} <= scopes

    def test_every_security_case_asserts_something(self):
        for case in GOLDEN.of_class(QuestionClass.SECURITY):
            assert case.expect_refusal or case.forbid or case.expect_empty or case.expect_rows, (
                case.id
            )

    def test_the_security_set_has_a_positive_control(self):
        # Every cross-scope case asserts a read returned nothing -- which a
        # completely broken system also does. At least one case must assert the
        # opposite, or the rest prove nothing.
        assert any(case.expect_rows for case in GOLDEN.of_class(QuestionClass.SECURITY))

    def test_every_hybrid_case_scripts_at_least_two_calls(self):
        for case in GOLDEN.of_class(QuestionClass.HYBRID):
            assert len(case.replay.calls) >= 2, case.id

    def test_every_document_case_searches(self):
        for case in GOLDEN.of_class(QuestionClass.DOCUMENT):
            assert any(call.tool == "search_documents" for call in case.replay.calls), case.id

    def test_every_case_that_expects_a_document_cites_a_marker(self):
        # An expectation the scripted answer never cites would score as a
        # retrieval failure for a reason that has nothing to do with retrieval.
        for case in GOLDEN.cases:
            if case.expect_documents:
                assert "[1]" in case.replay.answer, case.id


class TestMalformedCasesAreRefused:
    def _write(self, tmp_path: Path, body: str) -> Path:
        (tmp_path / "cases.yaml").write_text(body, encoding="utf-8")
        return tmp_path

    def test_an_unrecognised_schema_version(self, tmp_path):
        with pytest.raises(GoldenSetError, match="schema_version"):
            load_golden_set(self._write(tmp_path, "schema_version: 9\ncases: []\n"))

    def test_a_directory_with_no_cases(self, tmp_path):
        with pytest.raises(GoldenSetError, match="no golden cases"):
            load_golden_set(self._write(tmp_path, "schema_version: 1\ncases: []\n"))

    def test_a_missing_directory(self, tmp_path):
        with pytest.raises(GoldenSetError, match="not a directory"):
            load_golden_set(tmp_path / "nowhere")

    def test_a_repeated_case_id(self, tmp_path):
        body = "schema_version: 1\ncases:\n" + 2 * (
            "  - id: same\n    question: q\n    question_class: structured\n"
            '    principal: self\n    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="repeats case id"):
            load_golden_set(self._write(tmp_path, body))

    def test_an_unknown_field(self, tmp_path):
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: structured\n"
            '    principal: self\n    expect_tolls: [a]\n    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="expect_tolls"):
            load_golden_set(self._write(tmp_path, body))

    def test_expecting_arguments_for_a_tool_that_is_never_called(self, tmp_path):
        # Measures nothing in replay mode, so it is refused rather than counted.
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: structured\n"
            "    principal: self\n"
            "    expect_arguments: {count_employees: {department: Sales}}\n"
            '    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="never calls"):
            load_golden_set(self._write(tmp_path, body))

    def test_a_control_query_with_nothing_to_check(self, tmp_path):
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: structured\n"
            '    principal: self\n    control_sql: "SELECT 1"\n    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="checks nothing"):
            load_golden_set(self._write(tmp_path, body))

    def test_no_dispatch_without_a_refusal(self, tmp_path):
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: security\n"
            '    principal: self\n    expect_no_dispatch: true\n    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="implies expect_refusal"):
            load_golden_set(self._write(tmp_path, body))

    def test_a_security_case_that_asserts_nothing(self, tmp_path):
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: security\n"
            '    principal: self\n    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="must assert something"):
            load_golden_set(self._write(tmp_path, body))

    def test_expecting_both_empty_and_rows(self, tmp_path):
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: security\n"
            "    principal: self\n    expect_empty: true\n    expect_rows: true\n"
            '    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="contradict"):
            load_golden_set(self._write(tmp_path, body))

    def test_both_sources_outside_a_hybrid_case(self, tmp_path):
        body = (
            "schema_version: 1\ncases:\n"
            "  - id: one\n    question: q\n    question_class: document\n"
            '    principal: self\n    expect_both_sources: true\n    replay: {answer: "a"}\n'
        )

        with pytest.raises(GoldenSetError, match="hybrid case"):
            load_golden_set(self._write(tmp_path, body))
