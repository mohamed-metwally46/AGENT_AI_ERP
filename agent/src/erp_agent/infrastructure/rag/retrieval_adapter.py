"""The only file that imports both the ERP agent and RAG's retrieval internals.

That is the point of it. Above this line the agent knows about passages and
citations; below it there is a retriever, a reranker and a vector store. Keeping
the two vocabularies apart in one place is what lets RAG stay independently
usable and lets Phase 6 change RAG's metadata without the agent noticing.

The security filter is passed *into* the retrieval request, so the vector store
applies it. There is no post-filtering step here, and adding one would be the
wrong fix for any leak found later: by the time a chunk has been retrieved it has
been read, and the design's whole claim is that the model never receives one it
may not read (Plan.md section 8.4).

Since Phase 6 that filter is a predicate over the chunk's own security metadata
rather than a list of permitted document ids, so the store decides per chunk and
this adapter no longer has to enumerate the corpus before every search. The
catalogue stays for two jobs it is still the right answer to: resolving a
document's human-readable title for a citation, and reporting how much the
principal was permitted to search.
"""

from __future__ import annotations

from rag.domain.errors import RAGError, RerankingError
from rag.domain.models.chunk import ScoredChunk
from rag.domain.models.filters import FieldFilter, FilterOperator
from rag.domain.models.metadata import MetadataField
from rag.domain.models.query import RetrievalRequest
from rag.domain.ports.reranker import Reranker
from rag.domain.ports.retriever import Retriever

from erp_agent.domain.errors import KnowledgeUnavailableError
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.ports.knowledge import (
    Citation,
    DocumentEvidence,
    KnowledgeBase,
    RetrievedPassage,
)
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue
from erp_agent.infrastructure.rag.security_filter import allowed_document_ids, retrieval_filter

__all__ = ["RagRetrievalAdapter"]

#: Candidates fetched before reranking narrows them. Mirrors RAG's own default
#: ratio: retrieve broadly, then let the cross-encoder choose.
_CANDIDATE_MULTIPLIER = 4
_MAX_CANDIDATES = 40

#: A document id nothing carries, so the warm-up matches no chunk.
_NOTHING = "__warm_up__"


class RagRetrievalAdapter(KnowledgeBase):
    """Retrieves document evidence through the existing RAG pipeline."""

    def __init__(
        self,
        *,
        retriever: Retriever,
        catalogue: DocumentCatalogue,
        reranker: Reranker | None = None,
    ) -> None:
        """Wire the adapter.

        Args:
            retriever: RAG's retrieval pipeline, behind its own port.
            catalogue: Which documents exist and who may read them.
            reranker: Optional cross-encoder. When absent, retrieval order
                stands.
        """
        self._retriever = retriever
        self._catalogue = catalogue
        self._reranker = reranker

    async def warm(self) -> None:
        """Load the embedding model now, rather than inside the first question.

        The model loads lazily on first use and takes several seconds, which is
        long enough to exceed the loop's per-tool ceiling. That was recorded as
        an operational note in Phase 5 and then actually happened: the first
        document case of the first evaluation run timed out, and nothing else
        did.

        Warming with a filter that matches nothing, so this cannot return a
        chunk to anybody -- there is no principal here, and a warm-up that
        retrieved real content would be a read with no scope behind it.

        Failures are swallowed. A cold model is slower, not unsafe, and a
        start-up that refused to serve because a warm-up query failed would be
        trading a real outage for a latency saving.
        """
        request = RetrievalRequest(
            query_text="warm up",
            top_k=1,
            filters=FieldFilter(MetadataField.DOCUMENT_ID, FilterOperator.EQ, _NOTHING),
        )
        try:
            await self._retriever.retrieve(request)
        except RAGError:
            return

    async def search(
        self,
        context: SecurityContext,
        *,
        query: str,
        top_k: int = 5,
    ) -> DocumentEvidence:
        """Search the documents this principal may read."""
        # The manifest's view of how much this principal was allowed to look at.
        # Reported, not enforced: since Phase 6 the enforcement is the metadata
        # predicate below, which the store applies to each chunk's own tags.
        permitted = allowed_document_ids(context, self._catalogue)

        request = RetrievalRequest(
            query_text=query,
            top_k=min(max(top_k * _CANDIDATE_MULTIPLIER, top_k), _MAX_CANDIDATES),
            filters=retrieval_filter(context),
        )

        try:
            candidates = await self._retriever.retrieve(request)
        except RAGError as exc:
            raise KnowledgeUnavailableError(
                "the document index could not be searched",
                context={"reason": type(exc).__name__},
            ) from exc

        ranked = await self._rerank(query, candidates, top_k)

        return DocumentEvidence(
            query=query,
            passages=tuple(
                self._to_passage(chunk, marker)
                for marker, chunk in enumerate(ranked[:top_k], start=1)
            ),
            searched_document_count=len(permitted),
        )

    async def _rerank(
        self,
        query: str,
        candidates: tuple[ScoredChunk, ...],
        top_k: int,
    ) -> tuple[ScoredChunk, ...]:
        """Narrow candidates, tolerating a reranker that is unavailable."""
        if not candidates or self._reranker is None:
            return candidates

        try:
            return await self._reranker.rerank(query, candidates, min(top_k, len(candidates)))
        except RerankingError:
            # Degrading to retrieval order is a quality loss, not a safety one:
            # the permission filter already ran, so every candidate here is one
            # this principal may read.
            return candidates

    def _to_passage(self, chunk: ScoredChunk, marker: int) -> RetrievedPassage:
        """Translate a RAG chunk into the agent's vocabulary.

        A chunk whose document is not catalogued cannot reach here -- the filter
        restricts retrieval to catalogued ids -- so a missing record would mean
        the catalogue changed mid-request. The title falls back to the filename
        rather than failing the whole search, because a citation with a weaker
        label is better than no answer.
        """
        metadata = chunk.chunk.metadata
        record = self._catalogue.get(metadata.document_id)

        return RetrievedPassage(
            text=chunk.chunk.text,
            score=float(chunk.score),
            citation=Citation(
                marker=marker,
                document_id=metadata.document_id,
                document_title=record.title if record is not None else metadata.filename,
                chunk_id=metadata.chunk_id,
                section=metadata.section or metadata.heading,
                page_number=metadata.page_number,
            ),
        )
