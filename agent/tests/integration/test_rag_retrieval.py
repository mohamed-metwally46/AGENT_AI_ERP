"""Real retrieval against the live Qdrant index, with the filter applied.

The unit suite proves the filter *renders* correctly. These prove it *excludes*
correctly, which is a different claim: a filter can be well-formed and still be
ignored by the store, applied to the wrong payload key, or silently dropped when
the retriever composes a hybrid query.

The collection holds documents this agent has never catalogued (a large PDF and a
scratch file, ingested by RAG's own development work). They make the strongest
possible negative test: real content, really indexed, that must never surface.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from rag.domain.models import GenerationParams, LLMResponse
from rag.domain.models.tools import LLMMessage, ToolCall, ToolDefinition
from rag.domain.ports.llm_client import LLMClient

from erp_agent.application.agent.loop import AgentLoop
from erp_agent.application.agent.tool_registry import build_agent_registry
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue
from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter
from tests.unit.application.test_hr_tools import FakeDirectory

pytestmark = [pytest.mark.integration, pytest.mark.security]

PARAMS = GenerationParams(temperature=0.0, max_output_tokens=512, timeout_s=30.0)


class ScriptedLLM(LLMClient):
    """Replays tool calls, then a final answer. Mirrors the structured suite's."""

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
        self.tool_payloads.extend(m.content for m in messages if m.role == "tool")
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(model_id="scripted", text="Done.")


def _call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        model_id="scripted",
        text="",
        tool_calls=(ToolCall(call_id="c1", name=name, arguments=arguments),),
    )


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CATALOGUE = DocumentCatalogue.from_file(_FIXTURES / "documents.yaml")

#: Ingested into the same collection but deliberately absent from the manifest.
UNCATALOGUED = {"b94f002a558ce019214b1076e6daa642", "6aea4492e95abb93e4d71f06c6cfe42b"}


@pytest.fixture(scope="module")
def ingested() -> dict[str, str]:
    """The document ids produced when the policy fixtures were ingested."""
    path = _FIXTURES / "documents" / "ingested.json"
    if not path.is_file():
        pytest.skip("the policy documents have not been ingested; see tests/fixtures/documents")
    ingested: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return ingested


@pytest.fixture
async def adapter() -> AsyncIterator[RagRetrievalAdapter]:
    """A retrieval adapter over the live RAG pipeline, or skip."""
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

    yield RagRetrievalAdapter(retriever=retriever, catalogue=CATALOGUE)


def _context(scope: DataScope, **overrides: Any) -> SecurityContext:
    defaults: dict[str, Any] = {
        "user_id": "1",
        "employee_id": "00000000-0000-0000-0000-000000000001",
        "scope": scope,
    }
    return SecurityContext(**{**defaults, **overrides})


class TestRetrievalWorks:
    async def test_a_policy_question_finds_the_policy(self, adapter, ingested):
        evidence = await adapter.search(
            _context(DataScope.SELF), query="How many annual leave days do employees get?"
        )

        assert not evidence.is_empty
        found = {passage.citation.document_id for passage in evidence.passages}
        assert ingested["annual_leave_policy.md"] in found

    async def test_every_passage_carries_a_resolvable_citation(self, adapter):
        evidence = await adapter.search(_context(DataScope.SELF), query="overtime rate weekend")

        assert evidence.passages
        for passage in evidence.passages:
            citation = passage.citation
            assert citation.marker >= 1
            assert citation.document_id
            assert citation.chunk_id
            # The title comes from the catalogue, so it is the human-readable
            # name rather than a hash.
            assert CATALOGUE.get(citation.document_id) is not None
            assert citation.document_title == CATALOGUE.records[citation.document_id].title

    async def test_markers_are_contiguous_and_one_based(self, adapter):
        # Phase 5's grounding depends on [n] resolving to a specific passage.
        evidence = await adapter.search(_context(DataScope.SELF), query="conduct", top_k=3)

        assert [p.marker for p in evidence.passages] == list(range(1, len(evidence.passages) + 1))

    async def test_top_k_is_respected(self, adapter):
        evidence = await adapter.search(_context(DataScope.SELF), query="leave", top_k=2)

        assert len(evidence.passages) <= 2


class TestUncataloguedDocumentsAreInvisible:
    @pytest.mark.parametrize("scope", list(DataScope))
    async def test_no_scope_can_reach_an_uncatalogued_document(self, adapter, scope):
        # Real, indexed content that the manifest does not describe. An untagged
        # document has no known audience, so it is visible to nobody -- including
        # organisation scope.
        evidence = await adapter.search(
            _context(
                scope,
                dept_id="00000000-0000-0000-0000-0000000000d9",
                allowed_branch_ids=frozenset({"00000000-0000-0000-0000-0000000000b1"}),
            ),
            query="the",
            top_k=8,
        )

        reached = {passage.citation.document_id for passage in evidence.passages}
        assert not reached & UNCATALOGUED, f"{scope} reached an uncatalogued document"


class TestCrossScopeRetrievalReturnsNothing:
    async def test_a_principal_who_may_read_nothing_gets_no_passages(self, adapter):
        empty_catalogue = DocumentCatalogue(records={}, source=None)
        from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter as Adapter

        narrow = Adapter(retriever=adapter._retriever, catalogue=empty_catalogue)

        evidence = await narrow.search(_context(DataScope.ORGANISATION), query="annual leave")

        assert evidence.passages == ()
        assert evidence.searched_document_count == 0

    async def test_zero_chunks_not_chunks_the_model_declines(self, adapter, ingested):
        # The distinction that matters: the passages are absent from the result,
        # not present-but-marked-forbidden.
        only_conduct = DocumentCatalogue(
            records={
                key: value
                for key, value in CATALOGUE.records.items()
                if key == ingested["code_of_conduct.md"]
            },
            source=None,
        )
        from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter as Adapter

        narrow = Adapter(retriever=adapter._retriever, catalogue=only_conduct)

        evidence = await narrow.search(
            _context(DataScope.SELF), query="How many annual leave days do employees get?", top_k=8
        )

        reached = {passage.citation.document_id for passage in evidence.passages}
        assert ingested["annual_leave_policy.md"] not in reached
        assert reached <= {ingested["code_of_conduct.md"]}

    async def test_the_empty_case_records_why_it_was_empty(self, adapter):
        # "found nothing" and "was allowed to look at nothing" are different
        # answers to "why was that unhelpful?".
        empty = DocumentCatalogue(records={}, source=None)
        from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter as Adapter

        blocked = await Adapter(retriever=adapter._retriever, catalogue=empty).search(
            _context(DataScope.SELF), query="anything"
        )
        searched = await adapter.search(_context(DataScope.SELF), query="zzzzqqqxyzzy nonsense")

        assert blocked.searched_document_count == 0
        assert searched.searched_document_count > 0


class TestThroughTheLoop:
    """Section 11's Phase 4 acceptance criterion 1, joined up.

    Everything above tests the adapter directly. This is the only place where
    the loop, the seventh tool, the real catalogue and the real index run
    together -- which is what "answers from the document with a working
    citation" actually claims. The model is scripted, for the reason the
    structured end-to-end suite gives: an assertion about a live model's tool
    choice is an assertion about that model on that day.
    """

    async def test_a_policy_question_reaches_the_document_and_comes_back_cited(
        self, adapter, ingested
    ):
        llm = ScriptedLLM(
            _call("search_documents", query="annual leave policy entitlement days"),
            LLMResponse(model_id="scripted", text="Employees accrue leave as set out in [1]."),
        )
        registry = build_agent_registry(directory=FakeDirectory(), knowledge=adapter)
        loop = AgentLoop(llm=llm, registry=registry, generation_params=PARAMS)

        answer = await loop.answer("What is the annual leave policy?", _context(DataScope.SELF))

        assert answer.trace.tools_used == ("search_documents",)
        assert not answer.trace.refusals
        # The passage the model was handed is the real document, and the marker
        # it cited resolves to it.
        rendered = llm.tool_payloads[0]
        assert "quoted passages" in rendered
        assert "[1] Annual Leave Policy" in rendered

    async def test_the_citation_the_model_is_given_resolves_to_a_real_chunk(
        self, adapter, ingested
    ):
        # A citation marker that resolves to nothing is a fabricated citation
        # with extra steps. Markers are assigned by the adapter from retrieved
        # chunks, never by the model, so this holds structurally -- assert it.
        evidence = await adapter.search(
            _context(DataScope.SELF), query="annual leave entitlement", top_k=3
        )

        assert evidence.passages
        for citation in evidence.citations:
            record = CATALOGUE.get(citation.document_id)
            assert record is not None
            assert citation.document_title == record.title
        assert ingested["annual_leave_policy.md"] in {c.document_id for c in evidence.citations}

    async def test_an_unanswerable_question_tells_the_model_not_to_improvise(self, adapter):
        llm = ScriptedLLM(
            _call("search_documents", query="the company submarine fleet maintenance schedule"),
            LLMResponse(
                model_id="scripted", text="That is not covered by the documents I can see."
            ),
        )
        registry = build_agent_registry(directory=FakeDirectory(), knowledge=adapter)

        await AgentLoop(llm=llm, registry=registry, generation_params=PARAMS).answer(
            "How do we service the submarine fleet?", _context(DataScope.SELF)
        )

        rendered = llm.tool_payloads[0]
        # Either nothing matched, or what matched is irrelevant; in both cases
        # the envelope has to be present rather than a bare empty payload.
        assert "quoted passages" in rendered or "do not answer from general knowledge" in rendered
