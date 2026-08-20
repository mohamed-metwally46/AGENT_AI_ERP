"""Conversation history: bounded, scoped, and deliberately forgetful.

A follow-up question ("and what about last month?") is unanswerable without the
turn before it, so some history has to survive between requests. What survives
is the interesting part, and §5.2 states the constraint plainly: the conversation
layer must **not persist restricted payloads**.

So a stored turn holds the question and the final answer, and nothing else. Tool
results are excluded on purpose. They are where the restricted material actually
is -- a leave balance, a salary band, a passage from a document the asker could
read at the time -- and keeping them would create a second store of exactly the
data the whole security layer exists to gate, one that no scope predicate
guards. The cost is that the model re-reads a tool when a follow-up needs its
figures, which is a round trip, not a leak.

Two bounds, both hard:

*Turns per conversation.* The oldest fall off. An unbounded history is an
unbounded prompt, and an unbounded prompt is an unbounded bill and a growing
attack surface for anything an earlier turn contained.

*Conversations retained.* The store is in-process and capacity-bounded, evicting
least-recently-used. A server that remembers every conversation forever is a
memory leak with a retention policy nobody wrote.

**A conversation belongs to one principal.** Conversation ids are chosen by the
caller, so the store keys on ``(identity, conversation_id)`` rather than on the
id alone. Two principals may use the same id and neither can see the other's,
and a caller who guesses or is told someone else's id simply starts their own
empty thread.

That is a deliberate improvement on the obvious design, which is to key on the
id and refuse a mismatch. Refusing *works* -- nobody reads anyone else's turns
-- but it turns "this id belongs to someone else" into an error a caller can
observe, and an error that fires only for ids that exist is an oracle for which
ids exist. Namespacing removes the question instead of answering it carefully.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "DEFAULT_MAX_CONVERSATIONS",
    "DEFAULT_MAX_TURNS",
    "Conversation",
    "ConversationStore",
    "Turn",
]

#: Turns kept per conversation. Three exchanges is enough for the follow-ups
#: this system is for ("...and for Sales?") without carrying a transcript.
DEFAULT_MAX_TURNS = 6

#: Conversations kept in the process. Bounded because this store is memory.
DEFAULT_MAX_CONVERSATIONS = 500


@dataclass(frozen=True, slots=True)
class Turn:
    """One question and the answer it received.

    Attributes:
        question: What the user asked.
        answer: The grounded answer text, after unresolvable citation markers
            were stripped.
        asked_at: When, in UTC.
    """

    question: str
    answer: str
    asked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Reject a turn that records nothing.

        Raises:
            ValueError: If either side is blank. A half-recorded turn read back
                as context is worse than no context: it presents a question as
                answered when it was not.
        """
        if not self.question.strip():
            raise ValueError("a turn must record the question")
        if not self.answer.strip():
            raise ValueError("a turn must record the answer")


@dataclass(frozen=True, slots=True)
class Conversation:
    """A bounded history belonging to one principal.

    Attributes:
        conversation_id: The caller's identifier for this thread.
        identity: Who it belongs to. Checked on every read.
        turns: Most recent last, oldest already evicted.
    """

    conversation_id: str
    identity: str
    turns: tuple[Turn, ...] = ()

    def __len__(self) -> int:
        """How many turns are retained."""
        return len(self.turns)

    def __iter__(self) -> Iterator[Turn]:
        """Iterate turns, oldest first."""
        return iter(self.turns)

    def extended(self, turn: Turn, *, max_turns: int) -> Conversation:
        """Return this conversation with one more turn, within the bound.

        Args:
            turn: What just happened.
            max_turns: Ceiling on retained turns.

        Returns:
            A new conversation. The oldest turns are dropped rather than the
            newest: a follow-up refers to what was just said.
        """
        kept = (*self.turns, turn)[-max_turns:] if max_turns > 0 else ()
        return Conversation(
            conversation_id=self.conversation_id, identity=self.identity, turns=kept
        )


class ConversationStore:
    """An in-process, capacity-bounded conversation store.

    In-process on purpose for this phase: a shared store is a deployment
    decision (which backend, whose retention policy, encrypted at rest by what)
    and inventing one here would be inventing a policy nobody reviewed. The
    interface is small enough to put behind a port when that decision is made.
    """

    def __init__(
        self,
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
    ) -> None:
        """Configure the bounds.

        Args:
            max_turns: Turns retained per conversation.
            max_conversations: Conversations retained in the process.

        Raises:
            ValueError: If either bound is not positive. A store with no ceiling
                is the thing this class exists to prevent.
        """
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if max_conversations < 1:
            raise ValueError("max_conversations must be >= 1")

        self._max_turns = max_turns
        self._max_conversations = max_conversations
        self._conversations: OrderedDict[tuple[str, str], Conversation] = OrderedDict()

    def __len__(self) -> int:
        """How many conversations are retained."""
        return len(self._conversations)

    def history(self, conversation_id: str | None, *, identity: str) -> tuple[Turn, ...]:
        """Read a conversation's turns.

        Args:
            conversation_id: The thread being continued, or ``None`` to start
                a new one.
            identity: Who is asking. Part of the key, not a filter applied
                afterwards.

        Returns:
            The retained turns, oldest first. Empty for an unknown id **and**
            for one another principal happens to be using -- indistinguishable
            to the caller, because "that conversation exists but is not yours"
            is itself a disclosure.
        """
        if conversation_id is None:
            return ()

        key = (identity, conversation_id)
        conversation = self._conversations.get(key)
        if conversation is None:
            return ()

        self._conversations.move_to_end(key)
        return conversation.turns

    def record(self, conversation_id: str, *, identity: str, turn: Turn) -> Conversation:
        """Append a turn, creating the conversation if it is new.

        Cannot conflict with another principal's thread of the same name: they
        are different entries, so a caller who supplies an id that is in use
        elsewhere simply starts their own.

        Args:
            conversation_id: The thread.
            identity: Who it belongs to.
            turn: What just happened.

        Returns:
            The conversation as stored.
        """
        key = (identity, conversation_id)
        base = self._conversations.get(key) or Conversation(
            conversation_id=conversation_id, identity=identity
        )
        updated = base.extended(turn, max_turns=self._max_turns)

        self._conversations[key] = updated
        self._conversations.move_to_end(key)
        self._evict()
        return updated

    def forget(self, conversation_id: str, *, identity: str) -> bool:
        """Drop this caller's conversation, if they have one by that name.

        Args:
            conversation_id: The thread to forget.
            identity: Who is asking.

        Returns:
            Whether anything was dropped. ``False`` when another principal has
            a thread of that name, which is the same answer as "no such
            thread".
        """
        key = (identity, conversation_id)
        if key not in self._conversations:
            return False
        del self._conversations[key]
        return True

    def _evict(self) -> None:
        """Drop least-recently-used conversations down to the ceiling."""
        while len(self._conversations) > self._max_conversations:
            self._conversations.popitem(last=False)


def as_exchanges(turns: Sequence[Turn]) -> tuple[tuple[str, str], ...]:
    """Render turns as ``(role, content)`` pairs for a prompt.

    Kept separate from the store so the store owns retention and nothing else.
    The pairs are plain strings rather than an LLM message type: the store is
    application-layer and must not acquire a vendor vocabulary to do its job.

    Args:
        turns: The retained turns, oldest first.

    Returns:
        Alternating user and assistant content.
    """
    return tuple(
        pair for turn in turns for pair in (("user", turn.question), ("assistant", turn.answer))
    )
