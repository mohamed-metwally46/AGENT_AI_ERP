"""Answering one ERP question, with history, within a scope.

The seam between the transport and the agent. It exists so the API route is
three lines and holds no policy: what history a question sees, what gets
recorded afterwards, and what the caller is told are decisions, and decisions do
not belong in a controller (Plan.md section 5.2).

The security context is threaded, never derived -- the same rule the agent loop
follows, restated here because this is where a request first meets it. There is
no parameter through which a caller could supply one; it arrives already
resolved from :class:`~erp_agent.application.security.Authenticate`.
"""

from __future__ import annotations

from dataclasses import dataclass

from rag.domain.models.tools import LLMMessage

from erp_agent.application.agent.loop import AgentLoop
from erp_agent.application.agent.trace import AgentTrace
from erp_agent.application.conversation import ConversationStore, Turn, as_exchanges
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.policies.grounding import SourceReference
from erp_agent.domain.ports.knowledge import Citation

__all__ = ["AnswerErpQuestion", "AnsweredQuestion"]


@dataclass(frozen=True, slots=True)
class AnsweredQuestion:
    """What one question produced.

    Attributes:
        text: The grounded answer, with unresolvable citation markers removed.
        sources: What the answer actually cited, in marker order.
        trace: What the agent did, already sanitised.
        model_id: Which model produced it.
        conversation_id: The thread this turn belongs to.
    """

    text: str
    sources: tuple[SourceReference, ...]
    trace: AgentTrace
    model_id: str
    conversation_id: str

    @property
    def citations(self) -> tuple[Citation, ...]:
        """The document citations among the cited sources."""
        return tuple(s.citation for s in self.sources if s.citation is not None)


class AnswerErpQuestion:
    """Runs one question through the agent, with bounded history."""

    def __init__(self, *, loop: AgentLoop, conversations: ConversationStore) -> None:
        """Wire the use case.

        Args:
            loop: The bounded agent loop.
            conversations: Where history lives, and what bounds it.
        """
        self._loop = loop
        self._conversations = conversations

    async def __call__(
        self,
        question: str,
        *,
        context: SecurityContext,
        identity: str,
        conversation_id: str,
    ) -> AnsweredQuestion:
        """Answer one question.

        Args:
            question: What was asked.
            context: The caller's resolved scope. Required, and the only source
                of authority.
            identity: Who is asking. Used to decide whose history this is --
                separate from ``context`` because a conversation belongs to an
                authenticated subject, not to a scope.
            conversation_id: The thread to continue or start.

        Returns:
            The answer, its sources and its trace.

        Raises:
            AgentLoopLimitError: If a bound was hit before an answer.
            LLMUnavailableError: If the model could not be reached.
        """
        history = self._conversations.history(conversation_id, identity=identity)

        answer = await self._loop.answer(
            question,
            context,
            history=[LLMMessage(role, content) for role, content in as_exchanges(history)],
        )

        # Recorded after the answer, so a question that raised leaves no turn
        # behind. A history containing a question whose answer never existed
        # would be read back as context the model then has to explain.
        self._conversations.record(
            conversation_id,
            identity=identity,
            turn=Turn(question=question, answer=answer.text),
        )

        return AnsweredQuestion(
            text=answer.text,
            sources=answer.sources,
            trace=answer.trace,
            model_id=answer.model_id,
            conversation_id=conversation_id,
        )
