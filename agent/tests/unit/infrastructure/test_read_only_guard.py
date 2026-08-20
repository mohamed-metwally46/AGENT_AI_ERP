"""The write probes cover every forbidden shape, and are harmless.

These are properties of the probe *definitions*, checkable without a database.
Whether the server actually refuses them is the integration suite's job.
"""

from __future__ import annotations

import pytest

from erp_agent.infrastructure.database.read_only_guard import WRITE_PROBES

pytestmark = pytest.mark.unit


class TestCoverage:
    @pytest.mark.parametrize(
        "shape", ["INSERT", "UPDATE", "DELETE", "CREATE TABLE", "ALTER TABLE", "TRUNCATE"]
    )
    def test_every_forbidden_shape_is_probed(self, shape):
        # Plan.md section 1 forbids each of these. A barrier claim that is not
        # exercised for a given shape is a barrier claim, not a barrier.
        assert shape in {label for label, _ in WRITE_PROBES}

    def test_the_task_1_1_write_attempts_are_all_present(self):
        labels = [label for label, _ in WRITE_PROBES]

        assert {"INSERT", "UPDATE", "DELETE", "CREATE TABLE", "ALTER TABLE"} <= set(labels)

    def test_labels_are_unique(self):
        labels = [label for label, _ in WRITE_PROBES]

        assert len(labels) == len(set(labels))


class TestProbesAreHarmless:
    @pytest.mark.parametrize(
        ("label", "statement"),
        [
            (label, statement)
            for label, statement in WRITE_PROBES
            if label in {"INSERT", "UPDATE", "DELETE"}
        ],
    )
    def test_every_dml_probe_matches_no_row(self, label, statement):
        # PostgreSQL checks privileges and transaction mode before it looks at
        # whether any row matches, so an empty probe proves exactly as much as a
        # destructive one. It also stops a NOT NULL or foreign-key violation
        # masquerading as a successful refusal.
        assert "WHERE false" in statement

    def test_the_ddl_probes_name_a_probe_specific_object(self):
        # So a half-applied DDL probe cannot collide with a real column.
        ddl = [
            statement for label, statement in WRITE_PROBES if label.startswith(("CREATE", "ALTER"))
        ]

        assert all("erp_agent_write_probe" in statement for statement in ddl)

    def test_the_probes_are_constants_not_assembled_from_input(self):
        # The one place in the system that constructs a write statement. It must
        # stay a literal tuple; anything parameterised here is a hole.
        assert all(isinstance(statement, str) for _, statement in WRITE_PROBES)
        assert all("%" not in statement and "{" not in statement for _, statement in WRITE_PROBES)
