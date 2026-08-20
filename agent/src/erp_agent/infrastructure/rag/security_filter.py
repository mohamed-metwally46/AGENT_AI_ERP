"""Turning a SecurityContext into a filter RAG can actually apply.

The output is a ``FilterExpression`` from ``rag.domain.models.filters`` -- RAG's
own vocabulary, not a second one -- and it is handed to ``RetrievalRequest`` so
the vector store applies it. Nothing is fetched and then discarded: the model
never receives a chunk it may not read, so it cannot leak one (Plan.md section
8.4).

**Why this filters on ``document_id`` and not on ``department``.**
``FILTERABLE_FIELDS`` has no department, branch, owner or access-level member
(Plan.md gap G3), so there is no field to compare against. The only scoping
primitive RAG offers today is ``document_id``, and the plan's answer for Phases
4 and 5 is to compute the allowed set server-side and pass it in (section 7.3).
Phase 6 adds the metadata fields and this module collapses to a handful of
``FieldFilter``s on them.

Two consequences of that, worth naming rather than discovering later:

* Scoping is per document. A document is readable or it is not; a document that
  mixes departments cannot be partially withheld.
* The allowed set is computed from a manifest, so a document that is ingested
  but *not* catalogued is invisible to everyone. That is the correct default --
  an untagged document has no known audience -- but it means ingestion and
  cataloguing have to stay in step, and the integration suite checks that they do.
"""

from __future__ import annotations

from rag.domain.models.filters import FieldFilter, FilterExpression, FilterOperator
from rag.domain.models.metadata import MetadataField

from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.infrastructure.rag.document_catalogue import (
    AccessLevel,
    DocumentCatalogue,
    DocumentRecord,
)

__all__ = ["allowed_document_ids", "retrieval_filter"]


def _may_read(record: DocumentRecord, context: SecurityContext) -> bool:
    """Whether one principal may read one document.

    Fails closed at every branch: each ``case`` either grants for a stated
    reason or falls through to ``False``.

    There is deliberately no catch-all ``case _``. ``AccessLevel`` is exhaustive
    and mypy proves it, so a new member makes this function a type error at the
    next check -- which is a louder guard than a default branch that would
    silently refuse it and leave a document unreadable for nobody-knows-why.
    """
    match record.access_level:
        case AccessLevel.PUBLIC:
            # Company policy. Everyone authenticated may read it -- an employee
            # who cannot read the leave policy cannot check their own leave.
            return True

        case AccessLevel.PRIVATE:
            return (
                record.owner_employee_number is not None
                and record.owner_employee_number == context.employee_id
            )

        case AccessLevel.TEAM:
            # Team documents follow the scope ladder as closely as a
            # document-level model allows. SELF and TEAM scope carry no
            # departmental reach, so they see nothing here.
            if context.scope is DataScope.ORGANISATION:
                return True
            if context.scope is DataScope.BRANCH:
                return record.branch is not None and record.branch in context.allowed_branch_ids
            if context.scope is DataScope.DEPARTMENT:
                return record.department is not None and record.department == context.dept_id
            return False


def allowed_document_ids(
    context: SecurityContext,
    catalogue: DocumentCatalogue,
) -> frozenset[str]:
    """Compute which documents a principal may retrieve from.

    Args:
        context: The caller's resolved scope. The only source of authority; no
            part of the question or the model's output contributes.
        catalogue: The document manifest.

    Returns:
        The readable document identifiers. Possibly empty, which is a real
        answer and must render as a filter that matches nothing rather than as
        no filter at all.
    """
    return frozenset(
        document_id
        for document_id, record in catalogue.records.items()
        if _may_read(record, context)
    )


def retrieval_filter(
    context: SecurityContext,
    catalogue: DocumentCatalogue,
) -> FilterExpression:
    """Build the filter to hand to RAG's retrieval request.

    Args:
        context: The caller's resolved scope.
        catalogue: The document manifest.

    Returns:
        A ``FieldFilter`` restricting retrieval to the readable documents.

    Raises:
        ValueError: If the principal may read nothing. Returning ``None`` here
            would mean "no filter", which the retriever reads as "search
            everything" -- the same failure mode the SQL predicate renderer
            refuses by never producing an empty clause. The caller handles the
            empty case explicitly instead; see ``RagRetrievalAdapter``.
    """
    permitted = allowed_document_ids(context, catalogue)
    if not permitted:
        raise ValueError(
            "this principal may read no documents; the caller must short-circuit "
            "rather than retrieve with an absent filter"
        )

    return FieldFilter(
        MetadataField.DOCUMENT_ID,
        FilterOperator.IN,
        tuple(sorted(permitted)),
    )
