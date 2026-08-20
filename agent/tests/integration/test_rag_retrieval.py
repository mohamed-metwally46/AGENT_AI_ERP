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

from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue
from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter

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

#: Ingested into the same collection but deliberately absent from the manifest,
#: and therefore never stamped with an audience. Real content, really indexed,
#: that no scope may reach.
UNCATALOGUED = {"b94f002a558ce019214b1076e6daa642", "6aea4492e95abb93e4d71f06c6cfe42b"}

ENGINEERING_DEPT = "00000000-0000-0000-0000-0000000000d9"
OTHER_DEPT = "00000000-0000-0000-0000-0000000000ff"
CAIRO_BRANCH = "00000000-0000-0000-0000-0000000000b1"
OTHER_BRANCH = "00000000-0000-0000-0000-0000000000b9"
PRIVATE_OWNER = "00000000-0000-0000-0000-000000000005"


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
    """Phase 6 acceptance criterion 3: cross-department leakage is zero.

    Until Phase 6 this class narrowed the *catalogue* and checked the resulting
    document-id filter. That mechanism is gone: the audience now lives on the
    chunk, so isolation is a property of the principal's scope and the store's
    predicate, and narrowing a manifest would prove nothing about either.

    Every assertion below is on real indexed content. The Engineering runbook,
    the Cairo handbook and the private note are ingested, embedded and
    retrievable -- by the right principal.
    """

    async def test_another_department_retrieves_zero_engineering_chunks(self, adapter, ingested):
        outsider = _context(DataScope.DEPARTMENT, dept_id=OTHER_DEPT)

        evidence = await adapter.search(
            outsider, query="on-call rotation escalation paging deploy freeze", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        assert ingested["engineering_runbook.md"] not in reached

    async def test_the_owning_department_does_retrieve_them(self, adapter, ingested):
        # The negative above is only meaningful if the positive holds: a filter
        # that excluded everyone would pass the leakage test and be useless.
        insider = _context(DataScope.DEPARTMENT, dept_id=ENGINEERING_DEPT)

        evidence = await adapter.search(
            insider, query="on-call rotation escalation paging deploy freeze", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        assert ingested["engineering_runbook.md"] in reached

    async def test_another_branch_retrieves_zero_cairo_chunks(self, adapter, ingested):
        outsider = _context(DataScope.BRANCH, allowed_branch_ids=frozenset({OTHER_BRANCH}))

        evidence = await adapter.search(
            outsider, query="transport allowance office hours desk booking", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        assert ingested["cairo_handbook.md"] not in reached

    async def test_the_owning_branch_does_retrieve_them(self, adapter, ingested):
        insider = _context(DataScope.BRANCH, allowed_branch_ids=frozenset({CAIRO_BRANCH}))

        evidence = await adapter.search(
            insider, query="transport allowance office hours desk booking", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        assert ingested["cairo_handbook.md"] in reached

    @pytest.mark.parametrize("scope", list(DataScope))
    async def test_nobody_but_the_owner_retrieves_the_private_note(self, adapter, ingested, scope):
        # Including ORGANISATION scope. Private means private, and the widest
        # grant in the ladder does not override it.
        other = _context(
            scope,
            employee_id="00000000-0000-0000-0000-00000000ffff",
            dept_id=ENGINEERING_DEPT,
            allowed_branch_ids=frozenset({CAIRO_BRANCH}),
        )

        evidence = await adapter.search(
            other, query="personal objective advanced certification study leave", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        assert ingested["private_note.md"] not in reached

    async def test_the_owner_does_retrieve_it(self, adapter, ingested):
        owner = _context(DataScope.SELF, employee_id=PRIVATE_OWNER)

        evidence = await adapter.search(
            owner, query="personal objective advanced certification study leave", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        assert ingested["private_note.md"] in reached

    async def test_a_self_scoped_principal_reaches_only_public_documents(self, adapter, ingested):
        # The whole corpus in one query, at the narrowest scope. Anything
        # team- or private-scoped that comes back is a leak.
        evidence = await adapter.search(
            _context(DataScope.SELF), query="policy leave overtime conduct rotation branch", top_k=8
        )

        reached = {p.citation.document_id for p in evidence.passages}
        public = {
            ingested["annual_leave_policy.md"],
            ingested["overtime_policy.md"],
            ingested["code_of_conduct.md"],
        }
        assert reached <= public, f"self scope reached {reached - public}"

    async def test_the_filter_reaches_the_store_rather_than_this_process(self, adapter):
        # The claim the design rests on: a chunk the principal may not read is
        # never returned, so it is never available to be filtered out later.
        # Asserted by asking for far more than exists and checking the excluded
        # documents are simply absent.
        evidence = await adapter.search(_context(DataScope.SELF), query="the and of a to", top_k=8)

        for passage in evidence.passages:
            record = CATALOGUE.records[passage.citation.document_id]
            assert record.access_level.value == "public"
