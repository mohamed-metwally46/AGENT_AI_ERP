"""Document retrieval through the existing RAG, scoped before it runs."""

from __future__ import annotations

from erp_agent.infrastructure.rag.document_catalogue import (
    AccessLevel,
    DocumentCatalogue,
    DocumentRecord,
    default_catalogue_path,
)
from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter
from erp_agent.infrastructure.rag.security_filter import allowed_document_ids, retrieval_filter

__all__ = [
    "AccessLevel",
    "DocumentCatalogue",
    "DocumentRecord",
    "RagRetrievalAdapter",
    "allowed_document_ids",
    "default_catalogue_path",
    "retrieval_filter",
]
