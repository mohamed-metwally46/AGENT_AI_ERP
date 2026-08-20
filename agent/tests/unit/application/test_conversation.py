"""Conversation history: what it keeps, what it refuses to keep, and whose it is.

The interesting tests here are about *absence*. A history store is easy to get
right in the happy case and easy to get wrong in three ways that matter:

* keeping restricted material, so the data the security layer gated ends up in
  a second store nothing guards;
* keeping it forever, so a server is a memory leak with no retention policy;
* handing it to whoever names the right conversation id, which turns a guessable
  string into a read of somebody else's answers.
"""

from __future__ import annotations

import pytest

from erp_agent.application.conversation import (
    Conversation,
    ConversationStore,
    Turn,
    as_exchanges,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

ALICE = "employee-a"
BOB = "employee-b"


def _turn(n: int) -> Turn:
    return Turn(question=f"question {n}", answer=f"answer {n}")


class TestABoundedHistory:
    def test_turns_come_back_oldest_first(self):
        store = ConversationStore()
        for n in (1, 2, 3):
            store.record("c1", identity=ALICE, turn=_turn(n))

        assert [t.question for t in store.history("c1", identity=ALICE)] == [
            "question 1",
            "question 2",
            "question 3",
        ]

    def test_the_oldest_turns_fall_off_first(self):
        # A follow-up refers to what was just said, so the newest survive.
        store = ConversationStore(max_turns=2)
        for n in (1, 2, 3):
            store.record("c1", identity=ALICE, turn=_turn(n))

        assert [t.question for t in store.history("c1", identity=ALICE)] == [
            "question 2",
            "question 3",
        ]

    def test_the_number_of_conversations_is_bounded(self):
        store = ConversationStore(max_conversations=2)
        for name in ("c1", "c2", "c3"):
            store.record(name, identity=ALICE, turn=_turn(1))

        assert len(store) == 2

    def test_the_least_recently_used_conversation_is_evicted(self):
        store = ConversationStore(max_conversations=2)
        store.record("c1", identity=ALICE, turn=_turn(1))
        store.record("c2", identity=ALICE, turn=_turn(1))
        store.history("c1", identity=ALICE)  # touches c1
        store.record("c3", identity=ALICE, turn=_turn(1))

        assert store.history("c1", identity=ALICE)
        assert store.history("c2", identity=ALICE) == ()

    def test_an_unbounded_store_cannot_be_constructed(self):
        with pytest.raises(ValueError, match="max_turns"):
            ConversationStore(max_turns=0)
        with pytest.raises(ValueError, match="max_conversations"):
            ConversationStore(max_conversations=0)

    def test_an_unknown_conversation_is_empty_rather_than_an_error(self):
        assert ConversationStore().history("never-seen", identity=ALICE) == ()

    def test_no_conversation_id_means_no_history(self):
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))

        assert store.history(None, identity=ALICE) == ()


class TestAConversationBelongsToOnePrincipal:
    def test_another_principal_reads_nothing(self):
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))

        assert store.history("c1", identity=BOB) == ()

    def test_someone_elses_conversation_is_indistinguishable_from_a_missing_one(self):
        # "that exists but is not yours" is itself a disclosure.
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))

        assert store.history("c1", identity=BOB) == store.history("no-such-id", identity=BOB)

    def test_reusing_another_principals_id_starts_a_separate_thread(self):
        # Not an error. An error that fires only for ids that exist is an
        # oracle for which ids exist; namespacing removes the question.
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))
        store.record("c1", identity=BOB, turn=_turn(2))

        assert [t.question for t in store.history("c1", identity=ALICE)] == ["question 1"]
        assert [t.question for t in store.history("c1", identity=BOB)] == ["question 2"]

    def test_writing_under_a_shared_id_leaves_the_other_intact(self):
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))
        store.record("c1", identity=BOB, turn=_turn(2))

        assert len(store.history("c1", identity=ALICE)) == 1

    def test_forgetting_someone_elses_conversation_does_nothing(self):
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))

        assert store.forget("c1", identity=BOB) is False
        assert store.history("c1", identity=ALICE)

    def test_a_principal_can_forget_their_own(self):
        store = ConversationStore()
        store.record("c1", identity=ALICE, turn=_turn(1))

        assert store.forget("c1", identity=ALICE) is True
        assert store.history("c1", identity=ALICE) == ()


class TestRestrictedPayloadsAreNotPersisted:
    """Section 5.2: the conversation layer must not persist restricted payloads."""

    def test_a_turn_holds_only_the_question_and_the_answer(self):
        # Tool results are where the restricted material actually is -- a leave
        # balance, a salary band, a passage from a document. There is no field
        # for them, which is stronger than a rule about not filling one in.
        import dataclasses

        assert {f.name for f in dataclasses.fields(Turn)} == {"question", "answer", "asked_at"}

    def test_a_conversation_holds_no_tool_output(self):
        import dataclasses

        assert {f.name for f in dataclasses.fields(Conversation)} == {
            "conversation_id",
            "identity",
            "turns",
        }

    def test_a_half_recorded_turn_is_refused(self):
        with pytest.raises(ValueError, match="record the question"):
            Turn(question="  ", answer="something")
        with pytest.raises(ValueError, match="record the answer"):
            Turn(question="something", answer="  ")


class TestRenderingForAPrompt:
    def test_turns_become_alternating_user_and_assistant_content(self):
        exchanges = as_exchanges([_turn(1), _turn(2)])

        assert exchanges == (
            ("user", "question 1"),
            ("assistant", "answer 1"),
            ("user", "question 2"),
            ("assistant", "answer 2"),
        )

    def test_no_turns_render_as_nothing(self):
        assert as_exchanges([]) == ()

    def test_the_pairs_carry_no_vendor_type(self):
        # The store is application-layer; acquiring an LLM message type to do
        # its job would drag a vendor vocabulary into it.
        assert all(isinstance(part, str) for pair in as_exchanges([_turn(1)]) for part in pair)
