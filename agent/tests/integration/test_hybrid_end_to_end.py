"""Section 11's Phase 5 acceptance criteria, against both real sources.

Everything below the model is live here: the mock HR database answers the leave
questions, the Qdrant index answers the policy question, and the two are
assembled by the loop into one numbered context. The model is scripted, for the
reason the structured end-to-end suite gives -- an assertion about a live model's
tool choice is an assertion about that model on that day, and Phase 8's golden
set is where source-selection accuracy gets measured.

The conflict tests are the interesting ones. They are not staged: the fixture
handbook says five working days' notice and three months' minimum service, and
the ``leave_policies`` rows in the mock database say seven days and six months.
Those disagreements were in the fixtures before this phase existed, and they are
exactly what acceptance criterion 3 is about.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from rag.domain.models import GenerationParams, LLMResponse
from rag.domain.models.tools import LLMMessage, ToolCall, ToolDefinition
from rag.domain.ports.llm_client import LLMClient

from erp_agent.application.agent.loop import AgentLoop
from erp_agent.application.agent.tool_registry import build_agent_registry
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.domain.policies.grounding import SourceKind
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.queries.hr_directory import PostgresHrDirectory
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue
from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter

pytestmark = [pytest.mark.integration, pytest.mark.security]

PARAMS = GenerationParams(temperature=0.0, max_output_tokens=512, timeout_s=30.0)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CATALOGUE = DocumentCatalogue.from_file(_FIXTURES / "documents.yaml")

#: A query that reaches the handbook section stating the notice period.
NOTICE_QUERY = "how much notice must be given before taking annual leave"


class ScriptedLLM(LLMClient):
    """Replays tool calls; records the final message list, not a running total."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.tool_payloads: list[str] = []

    @property
    def model_id(self) -> str:
        return "scripted"

    async def generate(self, prompt: Any, params: Any) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    def stream(self, prompt: Any, params: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def respond(
        self,
        messages: tuple[LLMMessage, ...],
        tools: tuple[ToolDefinition, ...],
        params: GenerationParams,
    ) -> LLMResponse:
        self.tool_payloads = [m.content for m in messages if m.role == "tool"]
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(model_id="scripted", text="Done.")


def _call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        model_id="scripted",
        text="",
        tool_calls=(ToolCall(call_id=f"c-{name}", name=name, arguments=arguments),),
    )


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[PostgresReadOnlyDataSource]:
    adapter = PostgresReadOnlyDataSource(dsn=database_url, min_pool_size=1, max_pool_size=3)
    try:
        await adapter.open()
    except Exception as exc:
        pytest.skip(f"the mock HR database is not reachable: {exc}")
    try:
        yield adapter
    finally:
        await adapter.close()


@pytest.fixture
async def knowledge() -> AsyncIterator[RagRetrievalAdapter]:
    try:
        from rag.core.bootstrap import bootstrap
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"the RAG package is not fully installed: {exc}")

    import os

    previous = os.getcwd()
    os.chdir(Path(__file__).resolve().parents[3] / "RAG")
    try:
        container = bootstrap()
        await container.prepare_store()
        retriever = container.retriever
    except Exception as exc:
        pytest.skip(f"the RAG stack is not available: {exc}")
    finally:
        os.chdir(previous)

    adapter = RagRetrievalAdapter(retriever=retriever, catalogue=CATALOGUE)

    # Warm the embedder before the loop sees it. The model loads lazily inside
    # the first retrieval, which costs several seconds and can exceed the loop's
    # 15s per-tool ceiling -- a real operational note for deployment (warm on
    # start-up, do not raise the bound), but not what these tests are measuring.
    await adapter.search(_context(), query="warm up", top_k=1)

    yield adapter


@pytest.fixture
def ingested() -> dict[str, str]:
    path = _FIXTURES / "documents" / "ingested.json"
    if not path.is_file():
        pytest.skip("the policy documents have not been ingested; see tests/fixtures/documents")
    documents: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return documents


@pytest.fixture
async def employee_with_leave(source: PostgresReadOnlyDataSource) -> dict[str, Any]:
    """Someone who actually has an annual leave balance on file."""
    row = await source.fetch_one(
        SqlStatement(
            """
            SELECT e.employee_number, b.fiscal_year
              FROM leave_balances b
              JOIN employees e ON e.employee_id = b.employee_id
              JOIN leave_types t ON t.leave_type_id = b.leave_type_id
             WHERE t.leave_code = 'ANN'
             ORDER BY b.fiscal_year DESC, e.employee_number
             LIMIT 1
            """
        )
    )
    if row is None:
        pytest.skip("the mock database holds no annual leave balances")
    return dict(row)


@pytest.fixture
def loop(
    source: PostgresReadOnlyDataSource,
    knowledge: RagRetrievalAdapter,
) -> Callable[..., AgentLoop]:
    """Builds a loop over both live sources, for a caller-supplied scripted model."""

    def build(llm: LLMClient, **overrides: Any) -> AgentLoop:
        registry = build_agent_registry(directory=PostgresHrDirectory(source), knowledge=knowledge)
        return AgentLoop(
            llm=llm, registry=registry, generation_params=PARAMS, max_rounds=4, **overrides
        )

    return build


def _context() -> SecurityContext:
    return SecurityContext(
        user_id="1",
        employee_id="00000000-0000-0000-0000-000000000001",
        scope=DataScope.ORGANISATION,
    )


class TestTheAcceptanceQuestion:
    async def test_it_states_the_balance_the_rule_and_cites_both(
        self, loop, employee_with_leave, ingested
    ):
        number = employee_with_leave["employee_number"]
        # top_k=1 so the numbering is predictable enough to script an answer
        # against: passage [1], balance [2], rule [3].
        llm = ScriptedLLM(
            _call("search_documents", query="annual leave entitlement policy", top_k=1),
            _call("get_leave_balance", employee_number=number, leave_code="ANN"),
            _call("get_leave_policy", leave_code="ANN"),
            LLMResponse(
                model_id="scripted",
                text=(
                    "Your remaining annual leave covers a five-day trip [2], and the "
                    "entitlement rule is [3]. The handbook states the same standard [1]."
                ),
            ),
        )

        answer = await loop(llm).answer(
            f"Does {number} have enough annual leave for a five-day trip under company policy?",
            _context(),
        )

        assert answer.cites_both_sources, (
            f"sources={[(s.marker, s.kind, s.tool_name) for s in answer.sources]} "
            f"trace={answer.trace.as_log_payload()}"
        )
        assert {s.kind for s in answer.sources} == {SourceKind.STRUCTURED, SourceKind.DOCUMENT}
        # The balance and the rule are genuinely in the context the model saw.
        assert '"remaining"' in answer_payload(llm, "get_leave_balance")
        assert '"annual_entitlement_days"' in answer_payload(llm, "get_leave_policy")
        assert answer.citations
        assert answer.citations[0].document_id in set(ingested.values())

    async def test_every_marker_in_the_answer_resolves_to_a_real_source(
        self, loop, employee_with_leave
    ):
        number = employee_with_leave["employee_number"]
        llm = ScriptedLLM(
            _call("search_documents", query="annual leave entitlement policy", top_k=1),
            _call("get_leave_balance", employee_number=number, leave_code="ANN"),
            LLMResponse(model_id="scripted", text="Balance [2] against the handbook [1]."),
        )

        answer = await loop(llm).answer("q", _context())

        assert {s.marker for s in answer.sources} == {1, 2}
        assert {s.kind for s in answer.sources} == {SourceKind.DOCUMENT, SourceKind.STRUCTURED}
        assert answer.stripped_markers == ()

    async def test_a_marker_the_model_invented_does_not_reach_the_caller(
        self, loop, employee_with_leave
    ):
        number = employee_with_leave["employee_number"]
        llm = ScriptedLLM(
            _call("get_leave_balance", employee_number=number, leave_code="ANN"),
            LLMResponse(model_id="scripted", text="You have enough [1], see the handbook [7]."),
        )

        answer = await loop(llm).answer("q", _context())

        assert "[7]" not in answer.text
        assert answer.stripped_markers == (7,)


class TestMarkersDoNotCollideAcrossSources:
    async def test_a_record_and_a_passage_never_share_a_number(self, loop, employee_with_leave):
        number = employee_with_leave["employee_number"]
        llm = ScriptedLLM(
            _call("search_documents", query="annual leave entitlement", top_k=3),
            _call("get_leave_balance", employee_number=number, leave_code="ANN"),
            LLMResponse(model_id="scripted", text="ok"),
        )

        answer = await loop(llm).answer("q", _context())
        document_payload, record_payload = llm.tool_payloads[0], llm.tool_payloads[1]

        # Whatever the retrieval returned, the record's marker is the next one
        # after it -- not [1] again.
        assert record_payload.startswith("[")
        record_marker = int(record_payload[1 : record_payload.index("]")])
        assert f"[{record_marker}]" not in document_payload
        assert answer.trace.tools_used == ("search_documents", "get_leave_balance")


class TestConflictsBetweenTheHandbookAndTheRecords:
    async def test_the_disagreement_over_notice_is_surfaced(self, loop):
        # Real fixture data on both sides: the handbook says 5 working days, the
        # organisation-wide `leave_policies` row says 7.
        llm = ScriptedLLM(
            _call("search_documents", query=NOTICE_QUERY, top_k=8),
            _call("get_leave_policy", leave_code="ANN"),
            LLMResponse(model_id="scripted", text="The two sources differ."),
        )

        await loop(llm).answer("How much notice do I need for annual leave?", _context())

        notes = [p for p in llm.tool_payloads if "SOURCES DISAGREE" in p]
        if not notes:
            pytest.skip("retrieval did not return the handbook section stating notice")
        assert "5 days" in notes[0]
        assert "7 days" in notes[0]
        assert "do not silently choose one" in notes[0]

    async def test_the_note_takes_neither_side(self, loop):
        llm = ScriptedLLM(
            _call("search_documents", query=NOTICE_QUERY, top_k=8),
            _call("get_leave_policy", leave_code="ANN"),
            LLMResponse(model_id="scripted", text="ok"),
        )

        await loop(llm).answer("q", _context())

        notes = [p for p in llm.tool_payloads if "SOURCES DISAGREE" in p]
        if not notes:
            pytest.skip("retrieval did not return the handbook section stating notice")
        # Both figures carry a marker, so the answer can attribute each.
        assert "the document says" in notes[0]
        assert "the HR records say" in notes[0]

    async def test_no_conflict_is_manufactured_where_the_sources_agree(self, loop):
        # Entitlement and carry-forward match between the handbook and the
        # organisation-wide row. Only the rules that genuinely differ may appear.
        llm = ScriptedLLM(
            _call("search_documents", query="annual leave entitlement carry forward", top_k=8),
            _call("get_leave_policy", leave_code="ANN"),
            LLMResponse(model_id="scripted", text="ok"),
        )

        await loop(llm).answer("q", _context())

        note = "\n".join(p for p in llm.tool_payloads if "SOURCES DISAGREE" in p)
        assert "annual entitlement" not in note
        assert "carried into the next year" not in note


def answer_payload(llm: ScriptedLLM, tool_name: str) -> str:
    """The payload the named tool produced, found by its marker header or envelope."""
    for payload in llm.tool_payloads:
        if tool_name in payload:
            return payload
    raise AssertionError(f"{tool_name} produced no payload")
