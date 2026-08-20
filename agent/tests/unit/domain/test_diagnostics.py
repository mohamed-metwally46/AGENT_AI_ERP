"""The probe reports a barrier as intact only when every condition holds."""

from __future__ import annotations

import pytest

from erp_agent.domain.models.diagnostics import DataSourceProbe, WriteAttempt

pytestmark = pytest.mark.unit


def _probe(**overrides: object) -> DataSourceProbe:
    """A probe describing a correctly locked-down connection."""
    defaults: dict[str, object] = {
        "server_version": "16.14",
        "database": "mock_hr",
        "role": "mock_hr_readonly",
        "transaction_read_only": True,
        "statement_timeout": "15s",
        "write_attempts": (
            WriteAttempt("INSERT", True, "25006: cannot execute INSERT in a read-only transaction"),
            WriteAttempt("UPDATE", True, "25006: cannot execute UPDATE in a read-only transaction"),
        ),
        "write_privileges": (),
        "is_superuser": False,
        "can_create_in_public": False,
    }
    return DataSourceProbe(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestBarrierIntact:
    def test_a_fully_locked_down_connection_is_intact(self):
        assert _probe().barrier_intact
        assert _probe().failures == ()

    def test_a_probe_that_tried_nothing_proves_nothing(self):
        # The most important case: an empty attempt list must not read as
        # success just because no failure was recorded.
        probe = _probe(write_attempts=())

        assert not probe.barrier_intact
        assert any("nothing was proved" in failure for failure in probe.failures)

    def test_an_accepted_write_breaks_the_barrier(self):
        probe = _probe(
            write_attempts=(
                WriteAttempt("INSERT", True, "refused"),
                WriteAttempt("UPDATE", False, "the server accepted it"),
            )
        )

        assert not probe.barrier_intact
        assert probe.accepted_writes == ("UPDATE",)
        assert any("accepted UPDATE" in failure for failure in probe.failures)

    def test_a_writable_transaction_breaks_the_barrier(self):
        probe = _probe(transaction_read_only=False)

        assert not probe.barrier_intact
        assert any("READ ONLY" in failure for failure in probe.failures)

    def test_held_write_grants_break_the_barrier_even_when_writes_were_refused(self):
        # This is the case the read-only transaction hides: the session refused
        # the write, but the role could do it in a session configured
        # differently. The two halves fail independently, so both are checked.
        probe = _probe(write_privileges=("employees:INSERT", "employees:UPDATE"))

        assert not probe.barrier_intact
        assert any("write privileges" in failure for failure in probe.failures)

    def test_superuser_breaks_the_barrier(self):
        probe = _probe(is_superuser=True)

        assert not probe.barrier_intact
        assert any("superuser" in failure for failure in probe.failures)

    def test_create_in_public_breaks_the_barrier(self):
        probe = _probe(can_create_in_public=True)

        assert not probe.barrier_intact
        assert any("create objects" in failure for failure in probe.failures)

    def test_many_privileges_are_summarised_rather_than_dumped(self):
        probe = _probe(write_privileges=tuple(f"t{i}:INSERT" for i in range(12)))

        (failure,) = [f for f in probe.failures if "write privileges" in f]
        assert "+7 more" in failure


class TestWriteAttempt:
    def test_an_attempt_must_be_labelled(self):
        with pytest.raises(ValueError, match="labelled"):
            WriteAttempt(label="  ", rejected=True, detail="x")

    def test_the_server_reason_is_retained(self):
        # Which reason it was distinguishes the privilege barrier from the
        # read-only-transaction barrier, and they fail independently.
        attempt = WriteAttempt("INSERT", True, "42501: permission denied for table employees")

        assert "permission denied" in attempt.detail
