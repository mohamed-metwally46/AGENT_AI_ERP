"""Turning a SecurityContext into a filter RAG can actually apply.

The output is a ``FilterExpression`` from ``rag.domain.models.filters`` -- RAG's
own vocabulary, not a second one -- and it is handed to ``RetrievalRequest`` so
the vector store applies it. Nothing is fetched and then discarded: the model
never receives a chunk it may not read, so it cannot leak one (Plan.md section
8.4).

**What changed in Phase 6.** Until now this filtered on ``document_id``, because
``FILTERABLE_FIELDS`` had no security field to compare against (gap G3) and the
plan's sanctioned interim was to compute the permitted set server-side and pass
it in. Phase 6 closed that gap additively: ``department``, ``branch``,
``module``, ``owner_id``, ``access_level`` and ``document_category`` now live on
the chunk payload, indexed, so the permission predicate runs *inside the store*
against the chunk's own tags rather than against a list this package maintains.

Three consequences, all improvements:

* Scoping is per **chunk**, not per document. A document that mixes audiences
  can now be partially withheld, which the document-id model could not express.
* The permitted set no longer has to be enumerated before every search, so a
  corpus that grows does not grow the filter.
* An untagged chunk is readable by nobody. Every branch of the predicate
  requires ``access_level`` to equal a stated value, and a chunk with no
  ``access_level`` matches none of them. That is the correct default -- a
  document nobody classified has no known audience -- and it is why the metadata
  change requires a re-ingest rather than being invisible to existing data.

:func:`allowed_document_ids` survives because the *manifest* is still what
decides a document's audience at ingestion time (gap S4: assign, never infer).
It states the policy once; :func:`retrieval_filter` states the same policy as a
store predicate. They must agree, and a test asserts they do over the whole
catalogue rather than trusting that two readings of one rule stayed in step.
"""

from __future__ import annotations

from rag.domain.models.filters import And, FieldFilter, FilterExpression, FilterOperator, Or
from rag.domain.models.metadata import MetadataField, SecurityTags

from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.infrastructure.rag.document_catalogue import (
    AccessLevel,
    DocumentCatalogue,
    DocumentRecord,
)

__all__ = ["allowed_document_ids", "retrieval_filter", "security_tags"]


def _may_read(record: DocumentRecord, context: SecurityContext) -> bool:
    """Whether one principal may read one document.

    The single statement of the policy. :func:`retrieval_filter` expresses the
    same rule as a store predicate, and the security suite asserts the two agree
    for every scope over the whole catalogue.

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
            # Team documents follow the scope ladder as closely as the tags
            # allow. SELF and TEAM scope carry no departmental reach, so they
            # see nothing here.
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
    """Which catalogued documents a principal may read.

    No longer the retrieval filter -- that is :func:`retrieval_filter`, which
    asks the store. This is the manifest's view of the same question, used to
    report how much the principal was permitted to search and to decide what to
    stamp at ingestion.

    Args:
        context: The caller's resolved scope. The only source of authority; no
            part of the question or the model's output contributes.
        catalogue: The document manifest.

    Returns:
        The readable document identifiers. Possibly empty, which is a real
        answer: it means the principal was allowed to search nothing.
    """
    return frozenset(
        document_id
        for document_id, record in catalogue.records.items()
        if _may_read(record, context)
    )


def security_tags(record: DocumentRecord, *, module: str | None = None) -> SecurityTags:
    """Translate a manifest entry into the tags stamped at ingestion.

    The manifest is where a document's audience is *decided* (gap S4: assign at
    ingestion, do not infer). This is the one place that decision crosses into
    RAG's vocabulary, so the two cannot drift into different spellings of
    ``public``.

    Args:
        record: The manifest entry.
        module: Functional area to stamp, where the caller knows one.

    Returns:
        The tags to stamp on every chunk of that document.
    """
    return SecurityTags(
        department=record.department,
        branch=record.branch,
        module=module,
        owner_id=record.owner_employee_number,
        access_level=record.access_level.value,
    )


def retrieval_filter(context: SecurityContext) -> FilterExpression:
    """Build the permission predicate to hand to RAG's retrieval request.

    One disjunction, one clause per way this principal is entitled to reach a
    chunk. Every clause pins ``access_level`` to a stated value, so a chunk with
    no audience satisfies none of them.

    Args:
        context: The caller's resolved scope.

    Returns:
        A filter restricting retrieval to chunks this principal may read.

    Raises:
        ValueError: If no clause could be built. Unreachable today, because
            every authenticated principal may read public documents -- but
            returning an empty disjunction would be read by the retriever as
            "no filter", which means "search everything". The one failure this
            module must never have is the one that looks like success, so the
            guard stays even while the branch that would trip it does not exist.
    """
    clauses: list[FilterExpression] = [_level(AccessLevel.PUBLIC)]

    team = _team_reach(context)
    if team is not None:
        clauses.append(team)

    clauses.append(
        And(
            (
                _level(AccessLevel.PRIVATE),
                FieldFilter(MetadataField.OWNER_ID, FilterOperator.EQ, context.employee_id),
            )
        )
    )

    if not clauses:  # pragma: no cover - see the docstring; the guard is the point
        raise ValueError("a permission filter must never be empty; that would search everything")

    return Or(tuple(clauses))


def _level(level: AccessLevel) -> FieldFilter:
    """Pin ``access_level`` to one value."""
    return FieldFilter(MetadataField.ACCESS_LEVEL, FilterOperator.EQ, level.value)


def _team_reach(context: SecurityContext) -> FilterExpression | None:
    """How far this principal reaches into team-scoped documents, if at all.

    Mirrors the ``TEAM`` branch of :func:`_may_read` exactly. ``None`` where the
    principal has no departmental reach -- ``SELF`` and ``TEAM`` scope carry
    none, and omitting the clause is what makes that mean "no team documents"
    rather than "all of them".
    """
    if context.scope is DataScope.ORGANISATION:
        return _level(AccessLevel.TEAM)

    if context.scope is DataScope.BRANCH and context.allowed_branch_ids:
        return And(
            (
                _level(AccessLevel.TEAM),
                FieldFilter(
                    MetadataField.BRANCH,
                    FilterOperator.IN,
                    tuple(sorted(context.allowed_branch_ids)),
                ),
            )
        )

    if context.scope is DataScope.DEPARTMENT and context.dept_id:
        return And(
            (
                _level(AccessLevel.TEAM),
                FieldFilter(MetadataField.DEPARTMENT, FilterOperator.EQ, context.dept_id),
            )
        )

    return None
