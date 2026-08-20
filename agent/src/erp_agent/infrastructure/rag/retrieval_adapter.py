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
"""

from __future__ import annotations

from rag.domain.errors import RAGError, RerankingError
from rag.domain.models.chunk import ScoredChunk
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

    async def search(
        self,
        context: SecurityContext,
        *,
        query: str,
        top_k: int = 5,
    ) -> DocumentEvidence:
        """Search the documents this principal may read."""
        permitted = allowed_document_ids(context, self._catalogue)

        if not permitted:
            # Short-circuit rather than retrieve with an absent filter. An
            # empty permitted set must mean "search nothing", and the one way
            # to be certain of that is not to issue the query.
            return DocumentEvidence(query=query, passages=(), searched_document_count=0)

        request = RetrievalRequest(
            query_text=query,
            top_k=min(max(top_k * _CANDIDATE_MULTIPLIER, top_k), _MAX_CANDIDATES),
            filters=retrieval_filter(context, self._catalogue),
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
