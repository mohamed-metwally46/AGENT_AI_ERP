"""Request and response bodies for the HTTP surface.

The request model is the load-bearing one, and it is defined by what it
*refuses*. Plan.md section 11's Phase 7 acceptance criterion 2 is that a caller
cannot influence their own scope through any request field, and there are two
ways to get that wrong: accept a scope field, or accept unknown fields and hope
nothing downstream reads them. ``extra="forbid"`` closes the second, so a body
carrying ``scope`` or ``employee_id`` is a 422 rather than a field that sits in
the parsed object waiting for someone to use it.

The reason to reject rather than ignore is that ignoring is invisible. A caller
who sends ``{"question": "...", "scope": "organisation"}`` and receives a
correct, narrowly-scoped answer has been told nothing about why. Rejecting says
the field is not part of the contract, and a test can assert on it.

Responses carry the answer, its citations and the tools that ran. The trace is
already sanitised by the time it reaches here, and what is exposed is narrower
still: tool *names* and counts, never arguments, never rows.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AskRequest",
    "AskResponse",
    "CitationOut",
    "ErrorResponse",
    "HealthResponse",
    "SourceOut",
    "ToolCallOut",
]


class AskRequest(BaseModel):
    """A question, and optionally the conversation it continues.

    Attributes:
        question: What to answer.
        conversation_id: The thread to continue. Omit to start a new one. A
            conversation belonging to a different principal reads as empty
            rather than as an error, because "that exists but is not yours" is
            itself a disclosure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: Annotated[str, Field(min_length=1, max_length=32_000)]
    conversation_id: Annotated[str | None, Field(default=None, min_length=1, max_length=128)] = None


class CitationOut(BaseModel):
    """Where one quoted passage came from.

    Attributes:
        marker: The ``[n]`` used in the answer text.
        document_id: Identifier of the source document.
        document_title: Human-readable title.
        chunk_id: The exact chunk, so a claim traces to the text supporting it.
        section: Nearest section heading, where one was found.
        page_number: One-based page, for paginated formats.
    """

    model_config = ConfigDict(frozen=True)

    marker: int
    document_id: str
    document_title: str
    chunk_id: str
    section: str | None = None
    page_number: int | None = None


class SourceOut(BaseModel):
    """One numbered source the answer cited.

    Attributes:
        marker: The ``[n]`` used in the answer text.
        kind: ``structured`` for a record, ``document`` for a passage.
        tool_name: Which tool produced it.
        label: How the source reads to a person.
    """

    model_config = ConfigDict(frozen=True)

    marker: int
    kind: str
    tool_name: str
    label: str


class ToolCallOut(BaseModel):
    """One tool call, as the caller is allowed to see it.

    Argument *values* are deliberately absent even though the trace redacts
    them: what a caller needs is which tools ran and whether any were refused,
    and the narrower the response, the fewer places a value can escape through.

    Attributes:
        tool_name: Which tool the model asked for.
        outcome: ``ok``, ``refused`` or ``failed``.
        dispatched: Whether the handler actually ran.
        round_index: Which loop round it happened in.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str
    outcome: str
    dispatched: bool
    round_index: int


class AskResponse(BaseModel):
    """A grounded answer and how it was produced.

    Attributes:
        answer: The answer text, with unresolvable citation markers removed.
        conversation_id: The thread this turn belongs to.
        citations: Document citations, in marker order.
        sources: Everything cited, records included.
        tools_used: Tools that actually ran, in order, without repeats.
        tool_calls: Every call, including refusals.
        model_id: Which model produced the answer.
        rounds: Model round-trips used.
    """

    model_config = ConfigDict(frozen=True)

    answer: str
    conversation_id: str
    citations: tuple[CitationOut, ...] = ()
    sources: tuple[SourceOut, ...] = ()
    tools_used: tuple[str, ...] = ()
    tool_calls: tuple[ToolCallOut, ...] = ()
    model_id: str = ""
    rounds: int = 0


class ErrorResponse(BaseModel):
    """A failure, described without describing the internals.

    Plan.md section 19: no SQL, no stack traces, no credentials, no internal
    paths, no implementation detail. The ``code`` is the stable domain error
    identifier, which is what a client should branch on; the message is written
    for a person and says what to do, not what broke.

    Attributes:
        code: Stable machine-readable identifier.
        message: What the caller can do about it.
        retryable: Whether the same request could plausibly succeed later.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    retryable: bool = False


class HealthResponse(BaseModel):
    """Whether the process is serving.

    Attributes:
        status: ``ok`` when the process is up.
        write_barrier: ``intact`` -- verified at start-up, before anything was
            served. Reported because a read-only claim nobody can check is not
            a claim.
        knowledge: ``enabled`` or ``disabled``, so a caller can tell whether a
            document question is answerable at all.
        tools: How many tools are registered.
    """

    model_config = ConfigDict(frozen=True)

    status: str
    write_barrier: str
    knowledge: str
    tools: int
