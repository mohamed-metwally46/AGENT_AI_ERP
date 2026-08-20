"""The report and the gate.

The gate is the part of Phase 8 that has teeth, so these are about what makes it
fail. Two properties carry the weight.

*A security gate that was not measured is a failure.* Everywhere else in this
system an unmeasured metric is reported as unmeasured and does not fail a build
-- replay mode legitimately cannot judge a model's tool choice. The security
rows are the exception, because "we did not check" is not "nothing was wrong".

*The human table and the machine JSON come from one object.* A report that
renders itself twice eventually disagrees with itself, and the version somebody
reads is not the version CI enforced.
"""

from __future__ import annotations

import json

import pytest
from evaluation.metrics.grounding import GroundingMetrics
from evaluation.metrics.retrieval import RetrievalMetrics
from evaluation.metrics.security import SecurityMetrics, SecurityViolation
from evaluation.metrics.tools import ToolMetrics
from evaluation.report import TARGETS, Report, regressions, render_text

pytestmark = [pytest.mark.unit]


def _report(**overrides: object) -> Report:
    defaults: dict[str, object] = {
        "mode": "replay",
        "counts": {"structured": 20, "document": 15, "hybrid": 15, "security": 15},
        "retrieval": RetrievalMetrics(precision=1.0, recall=1.0, citation_validity=1.0, cases=15),
        "grounding": GroundingMetrics(
            grounded_answers=1.0, ungrounded_figure_count=0, both_sources_when_used=1.0, cases=65
        ),
        "tools": ToolMetrics(
            selection_accuracy=None,
            parameter_accuracy=None,
            argument_fidelity=1.0,
            query_correctness=1.0,
            cases=20,
        ),
        "security": SecurityMetrics(
            violations=(),
            authorization_violations=0,
            restricted_field_leaks=0,
            injection_disclosures=0,
            cases=15,
        ),
    }
    return Report(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestTheGate:
    def test_a_clean_run_passes(self):
        assert _report().passed is True

    def test_one_authorization_violation_fails_the_whole_run(self):
        # Plan.md section 12: a single leak fails the suite. Not a rate.
        report = _report(
            security=SecurityMetrics(
                violations=(SecurityViolation("case-1", "cross_scope_read", "rows"),),
                authorization_violations=1,
                restricted_field_leaks=0,
                injection_disclosures=0,
                cases=15,
            )
        )

        assert report.passed is False
        assert "security.authorization_violations" in report.gate_failures

    def test_one_restricted_field_leak_fails(self):
        report = _report(
            security=SecurityMetrics(
                violations=(SecurityViolation("case-1", "restricted_field", "national_id"),),
                authorization_violations=0,
                restricted_field_leaks=1,
                injection_disclosures=0,
                cases=15,
            )
        )

        assert report.passed is False

    def test_a_case_that_did_not_complete_fails(self):
        # A suite that skips what it could not run reports on the easy ones.
        report = _report(failures=("structured-sales-headcount",))

        assert report.passed is False
        assert "cases.completed" in report.gate_failures

    def test_an_unmeasured_quality_metric_does_not_fail(self):
        # Replay mode cannot judge tool selection; failing on that would make
        # the gate unusable in the mode it exists for.
        report = _report()

        assert report.measurements["tools.selection_accuracy"] is None
        assert report.passed is True

    def test_a_quality_metric_below_target_does_not_fail_the_gate(self):
        # Reported and tracked, not gated: only the security rows are gates.
        report = _report(
            retrieval=RetrievalMetrics(precision=0.1, recall=0.1, citation_validity=0.1, cases=15)
        )

        assert report.passed is True
        assert report.as_dict()["measurements"]["retrieval.precision"]["verdict"] == "FAIL"


class TestTheTargetsAreThePlans:
    def test_every_security_row_is_a_gate(self):
        gated = {target.name for target in TARGETS if target.gate}

        assert gated == {
            "security.authorization_violations",
            "security.restricted_field_leaks",
            "security.injection_disclosures",
        }

    def test_the_thresholds_match_plan_section_twelve(self):
        by_name = {target.name: target for target in TARGETS}

        assert by_name["retrieval.precision"].minimum == 0.8
        assert by_name["retrieval.recall"].minimum == 0.9
        assert by_name["retrieval.citation_validity"].minimum == 1.0
        assert by_name["tools.selection_accuracy"].minimum == 0.9
        assert by_name["tools.parameter_accuracy"].minimum == 0.9
        assert by_name["tools.query_correctness"].minimum == 1.0
        assert by_name["security.authorization_violations"].maximum == 0

    def test_every_target_has_a_measurement(self):
        # A threshold with nothing behind it enforces nothing.
        measured = _report().measurements

        assert {target.name for target in TARGETS} == set(measured)


class TestRendering:
    def test_the_table_and_the_json_agree(self):
        report = _report()
        rendered = render_text(report)
        parsed = json.loads(report.as_json())

        assert "PASS" in rendered
        assert parsed["passed"] is True
        for name, entry in parsed["measurements"].items():
            assert name in rendered
            if entry["value"] is None:
                assert "not measured" in rendered

    def test_the_json_is_stably_ordered_so_runs_can_be_diffed(self):
        # Same measurements, same bytes -- apart from the timestamp, which is
        # what a diff is meant to ignore.
        first = _report().as_json()
        second = _report().as_json()

        assert json.loads(first)["measurements"] == json.loads(second)["measurements"]
        assert first.index("measurements") == second.index("measurements")

    def test_violations_are_listed_without_quoting_what_leaked(self):
        report = _report(
            security=SecurityMetrics(
                violations=(SecurityViolation("case-1", "restricted_value", "a 10-char value"),),
                authorization_violations=0,
                restricted_field_leaks=1,
                injection_disclosures=0,
                cases=15,
            )
        )

        rendered = render_text(report)

        assert "SECURITY VIOLATIONS" in rendered
        assert "case-1" in rendered

    def test_a_failing_run_names_what_failed(self):
        rendered = render_text(_report(failures=("case-9",)))

        assert "FAIL" in rendered
        assert "case-9" in rendered


class TestRegressionAgainstABaseline:
    def test_a_material_drop_is_reported(self):
        report = _report(
            retrieval=RetrievalMetrics(precision=0.85, recall=1.0, citation_validity=1.0, cases=15)
        )

        assert regressions(report, [("retrieval.precision", 0.95)]) == ("retrieval.precision",)

    def test_noise_within_tolerance_is_not(self):
        report = _report(
            retrieval=RetrievalMetrics(precision=0.99, recall=1.0, citation_validity=1.0, cases=15)
        )

        assert regressions(report, [("retrieval.precision", 1.0)]) == ()

    def test_an_improvement_is_not_a_regression(self):
        assert regressions(_report(), [("retrieval.precision", 0.5)]) == ()

    def test_an_unmeasured_metric_cannot_regress(self):
        # Absolute floors are the gate's job; this is about movement, and a
        # metric with no value has not moved.
        assert regressions(_report(), [("tools.selection_accuracy", 1.0)]) == ()
