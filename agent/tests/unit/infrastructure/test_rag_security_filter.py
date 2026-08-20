"""Translating a scope into a retrieval filter RAG can apply.

Phase 6 changed what that filter *is*. It used to be a membership test over
document ids computed here; it is now a predicate over the security metadata
each chunk carries, evaluated by the store. The tests changed with it, and three
things are worth stating about what they check.

*The policy is stated twice and must mean the same thing.* ``_may_read`` decides
a document's audience from the manifest, at ingestion; ``retrieval_filter``
decides it from the chunk's tags, at retrieval. Two readings of one rule can
drift, so ``TestTheTwoStatementsOfThePolicyAgree`` evaluates the filter against
every catalogued document and asserts it selects exactly what ``_may_read``
selects, for every scope.

*An untagged chunk is readable by nobody.* Not by accident -- every clause pins
``access_level`` to a stated value, so absence matches nothing. This is the
property that keeps the pre-Phase-6 corpus invisible until it is re-ingested,
and it is asserted directly rather than assumed.

*The filter is never empty.* An empty disjunction would be handed to the
retriever as "no filter", which means "search everything". The one failure this
module must never have is the one that looks like success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rag.domain.models.filters import And, FieldFilter, FilterExpression, FilterOperator, Not, Or
from rag.domain.models.metadata import FILTERABLE_FIELDS, MetadataField, SecurityTags
from rag.domain.policies import derive_document_id

from erp_agent.domain.errors import ConfigurationError
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue, DocumentRecord
from erp_agent.infrastructure.rag.security_filter import (
    allowed_document_ids,
    retrieval_filter,
    security_tags,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

CATALOGUE = DocumentCatalogue.from_file(
    Path(__file__).resolve().parents[2] / "fixtures" / "documents.yaml"
)

#: Derived rather than written out, because these are the ids the *chunks* will
#: carry: a constant copied by hand would let the manifest and the index drift
#: apart while every test still passed.
LEAVE_POLICY = derive_document_id("annual_leave_policy.md")
OVERTIME_POLICY = derive_document_id("overtime_policy.md")
CODE_OF_CONDUCT = derive_document_id("code_of_conduct.md")
ENGINEERING_RUNBOOK = derive_document_id("engineering_runbook.md")
CAIRO_HANDBOOK = derive_document_id("cairo_handbook.md")
PRIVATE_NOTE = derive_document_id("private_note.md")

PUBLIC = {LEAVE_POLICY, OVERTIME_POLICY, CODE_OF_CONDUCT}
ENGINEERING_DEPT = "00000000-0000-0000-0000-0000000000d9"
CAIRO_BRANCH = "00000000-0000-0000-0000-0000000000b1"
PRIVATE_OWNER = "00000000-0000-0000-0000-000000000005"


def _context(scope: DataScope, **overrides: object) -> SecurityContext:
    defaults: dict[str, object] = {
        "user_id": "1",
        "employee_id": "00000000-0000-0000-0000-000000000001",
        "scope": scope,
    }
    return SecurityContext(**{**defaults, **overrides})  # type: ignore[arg-type]


def _payload(record: DocumentRecord) -> dict[str, Any]:
    """The security payload a chunk of this document carries once ingested."""
    tags = security_tags(record, module="HR")
    return {
        MetadataField.ACCESS_LEVEL: tags.access_level,
        MetadataField.DEPARTMENT: tags.department,
        MetadataField.BRANCH: tags.branch,
        MetadataField.OWNER_ID: tags.owner_id,
        MetadataField.MODULE: tags.module,
    }


def _matches(expression: FilterExpression, payload: dict[str, Any]) -> bool:
    """Evaluate a domain filter against one chunk's payload.

    A deliberately literal reading of the expression tree, written here rather
    than imported so the test does not prove the filter correct by reusing the
    same translator it is meant to check.
    """
    match expression:
        case FieldFilter():
            actual = payload.get(expression.field)
            match expression.operator:
                case FilterOperator.EQ:
                    return actual == expression.value
                case FilterOperator.NE:
                    return actual != expression.value
                case FilterOperator.IN:
                    return actual in expression.value
                case FilterOperator.NOT_IN:
                    return actual not in expression.value
                case FilterOperator.IS_NULL:
                    return actual is None
                case FilterOperator.IS_NOT_NULL:
                    return actual is not None
                case _:
                    raise AssertionError(f"unsupported in this evaluator: {expression.operator}")
        case And():
            return all(_matches(clause, payload) for clause in expression.clauses)
        case Or():
            return any(_matches(clause, payload) for clause in expression.clauses)
        case Not():
            return not _matches(expression.clause, payload)
        case _:
            raise AssertionError(f"unsupported node: {type(expression)}")


def _selected(context: SecurityContext, catalogue: DocumentCatalogue = CATALOGUE) -> set[str]:
    """Which catalogued documents the filter would let through."""
    expression = retrieval_filter(context)
    return {
        document_id
        for document_id, record in catalogue.records.items()
        if _matches(expression, _payload(record))
    }


class TestTheBlockerIsClosed:
    def test_rag_can_now_filter_on_every_security_field(self):
        # The inverse of the Phase 4 test, which asserted none of these existed
        # (Plan.md gap G3). Phase 6 added them; this is the acceptance criterion.
        names = {field.value for field in FILTERABLE_FIELDS}

        assert {"department", "branch", "owner_id", "access_level", "module"} <= names
        assert "document_category" in names

    def test_the_change_was_additive(self):
        # Every field that was filterable before Phase 6 still is. An "additive"
        # change that quietly dropped one would break callers this package
        # never sees.
        before = {
            "document_id",
            "chunk_id",
            "ingest_version",
            "filename",
            "document_type",
            "page_number",
            "section",
            "heading",
            "heading_path",
            "author",
            "title",
            "created_at",
            "ingested_at",
            "language",
            "chunk_index",
        }

        assert before <= {field.value for field in FILTERABLE_FIELDS}

    def test_document_id_is_still_filterable(self):
        # The Phase 4 mechanism is not removed, only no longer relied on.
        assert MetadataField.DOCUMENT_ID in FILTERABLE_FIELDS


class TestTheTwoStatementsOfThePolicyAgree:
    """The manifest view and the store predicate must select the same documents."""

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_they_agree_for_a_principal_with_full_reach(self, scope):
        context = _context(
            scope, dept_id=ENGINEERING_DEPT, allowed_branch_ids=frozenset({CAIRO_BRANCH})
        )

        assert _selected(context) == set(allowed_document_ids(context, CATALOGUE))

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_they_agree_for_the_owner_of_the_private_document(self, scope):
        context = _context(
            scope,
            employee_id=PRIVATE_OWNER,
            dept_id=ENGINEERING_DEPT,
            allowed_branch_ids=frozenset({CAIRO_BRANCH}),
        )

        assert _selected(context) == set(allowed_document_ids(context, CATALOGUE))

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_they_agree_for_a_principal_with_no_reach_at_all(self, scope):
        context = _context(
            scope,
            employee_id="00000000-0000-0000-0000-00000000ffff",
            dept_id="00000000-0000-0000-0000-0000000000ff",
            allowed_branch_ids=frozenset({"00000000-0000-0000-0000-0000000000zz"}),
        )

        assert _selected(context) == set(allowed_document_ids(context, CATALOGUE))


class TestPublicDocuments:
    @pytest.mark.parametrize("scope", list(DataScope))
    def test_company_policy_is_readable_at_every_scope(self, scope):
        # An employee who cannot read the leave policy cannot check their own
        # leave.
        context = _context(
            scope, dept_id=ENGINEERING_DEPT, allowed_branch_ids=frozenset({CAIRO_BRANCH})
        )

        assert _selected(context) >= PUBLIC


class TestTeamDocuments:
    def test_a_department_scoped_principal_reads_their_own_departments(self):
        context = _context(DataScope.DEPARTMENT, dept_id=ENGINEERING_DEPT)

        assert ENGINEERING_RUNBOOK in _selected(context)

    def test_a_department_scoped_principal_does_not_read_another_departments(self):
        context = _context(DataScope.DEPARTMENT, dept_id="00000000-0000-0000-0000-0000000000ff")

        assert ENGINEERING_RUNBOOK not in _selected(context)

    def test_a_branch_scoped_principal_reads_their_branch(self):
        context = _context(DataScope.BRANCH, allowed_branch_ids=frozenset({CAIRO_BRANCH}))

        assert CAIRO_HANDBOOK in _selected(context)

    def test_a_branch_scoped_principal_does_not_read_another_branch(self):
        context = _context(
            DataScope.BRANCH, allowed_branch_ids=frozenset({"00000000-0000-0000-0000-0000000000zz"})
        )

        assert CAIRO_HANDBOOK not in _selected(context)

    @pytest.mark.parametrize("scope", [DataScope.SELF, DataScope.TEAM])
    def test_narrow_scopes_reach_no_team_document(self, scope):
        # SELF and TEAM carry no departmental reach, so the team clause is
        # omitted entirely -- which is what makes its absence mean "none"
        # rather than "all".
        assert _selected(_context(scope)) == PUBLIC

    def test_organisation_scope_reads_every_team_document(self):
        selected = _selected(_context(DataScope.ORGANISATION))

        assert ENGINEERING_RUNBOOK in selected
        assert CAIRO_HANDBOOK in selected


class TestPrivateDocuments:
    def test_only_the_owner_reads_a_private_document(self):
        assert PRIVATE_NOTE in _selected(_context(DataScope.SELF, employee_id=PRIVATE_OWNER))

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_nobody_else_reads_it_at_any_scope(self, scope):
        # Not even organisation scope: private means private.
        other = _context(
            scope,
            employee_id="00000000-0000-0000-0000-00000000ffff",
            dept_id=ENGINEERING_DEPT,
            allowed_branch_ids=frozenset({CAIRO_BRANCH}),
        )

        assert PRIVATE_NOTE not in _selected(other)


class TestAnUntaggedChunkIsReadableByNobody:
    """The property that keeps a pre-Phase-6 index invisible until re-ingested."""

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_no_scope_matches_a_chunk_with_no_security_metadata(self, scope):
        context = _context(
            scope,
            dept_id=ENGINEERING_DEPT,
            allowed_branch_ids=frozenset({CAIRO_BRANCH}),
        )
        untagged: dict[str, Any] = {
            MetadataField.ACCESS_LEVEL: None,
            MetadataField.DEPARTMENT: None,
            MetadataField.BRANCH: None,
            MetadataField.OWNER_ID: None,
        }

        assert not _matches(retrieval_filter(context), untagged)

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_a_chunk_whose_keys_are_absent_entirely_also_matches_nothing(self, scope):
        # A chunk written before the fields existed has no such keys at all,
        # which is a different shape from "present and null".
        context = _context(
            scope, dept_id=ENGINEERING_DEPT, allowed_branch_ids=frozenset({CAIRO_BRANCH})
        )

        assert not _matches(retrieval_filter(context), {})

    def test_a_department_tag_alone_does_not_make_a_chunk_readable(self):
        # access_level is pinned in every clause, so a chunk tagged with a
        # matching department but no audience still matches nothing.
        context = _context(DataScope.DEPARTMENT, dept_id=ENGINEERING_DEPT)
        payload: dict[str, Any] = {
            MetadataField.ACCESS_LEVEL: None,
            MetadataField.DEPARTMENT: ENGINEERING_DEPT,
        }

        assert not _matches(retrieval_filter(context), payload)


class TestTheRenderedFilter:
    def test_every_clause_pins_the_access_level(self):
        # The structural reason absence matches nothing.
        expression = retrieval_filter(_context(DataScope.ORGANISATION))

        assert isinstance(expression, Or)
        for clause in expression.clauses:
            fields = _fields_in(clause)
            assert MetadataField.ACCESS_LEVEL in fields, clause

    def test_it_never_renders_an_empty_disjunction(self):
        # An empty Or would be "no filter", which the retriever reads as
        # "search everything". `Or` refuses to be constructed empty, and the
        # builder never tries.
        for scope in DataScope:
            expression = retrieval_filter(
                _context(
                    scope,
                    dept_id=ENGINEERING_DEPT,
                    allowed_branch_ids=frozenset({CAIRO_BRANCH}),
                )
            )
            assert isinstance(expression, Or)
            assert expression.clauses

    def test_it_carries_no_field_the_store_cannot_index(self):
        # FieldFilter enforces this at construction; asserting it here means a
        # future clause on an unindexed field fails as a test rather than as a
        # full scan in production.
        expression = retrieval_filter(_context(DataScope.BRANCH, allowed_branch_ids={CAIRO_BRANCH}))

        assert _fields_in(expression) <= FILTERABLE_FIELDS

    def test_the_private_clause_names_the_caller_and_not_a_request_value(self):
        context = _context(DataScope.SELF, employee_id=PRIVATE_OWNER)

        expression = retrieval_filter(context)

        owners = _values_for(expression, MetadataField.OWNER_ID)
        assert owners == {PRIVATE_OWNER}


def _fields_in(expression: FilterExpression) -> set[MetadataField]:
    match expression:
        case FieldFilter():
            return {expression.field}
        case And() | Or():
            return {f for clause in expression.clauses for f in _fields_in(clause)}
        case Not():
            return _fields_in(expression.clause)
        case _:  # pragma: no cover
            raise AssertionError(type(expression))


def _values_for(expression: FilterExpression, field: MetadataField) -> set[Any]:
    match expression:
        case FieldFilter():
            return {expression.value} if expression.field is field else set()
        case And() | Or():
            return {v for clause in expression.clauses for v in _values_for(clause, field)}
        case Not():
            return _values_for(expression.clause, field)
        case _:  # pragma: no cover
            raise AssertionError(type(expression))


class TestTagsStampedAtIngestion:
    def test_a_public_document_is_tagged_public(self):
        record = CATALOGUE.records[LEAVE_POLICY]

        tags = security_tags(record)

        assert tags.access_level == "public"
        assert tags.department is None
        assert tags.owner_id is None

    def test_a_team_document_carries_its_department(self):
        tags = security_tags(CATALOGUE.records[ENGINEERING_RUNBOOK])

        assert tags.access_level == "team"
        assert tags.department == ENGINEERING_DEPT

    def test_a_private_document_carries_its_owner(self):
        tags = security_tags(CATALOGUE.records[PRIVATE_NOTE])

        assert tags.access_level == "private"
        assert tags.owner_id == PRIVATE_OWNER

    def test_the_module_is_stamped_when_the_caller_states_one(self):
        assert security_tags(CATALOGUE.records[PRIVATE_NOTE], module="HR").module == "HR"

    def test_the_vocabulary_matches_the_manifest_rather_than_a_second_spelling(self):
        # `security_tags` is the one crossing point between the manifest and
        # RAG's payload; if it invented its own words for `public`, the filter
        # would silently match nothing.
        for record in CATALOGUE.records.values():
            assert security_tags(record).access_level == record.access_level.value

    def test_the_tags_are_a_rag_type_not_a_second_one(self):
        assert isinstance(security_tags(CATALOGUE.records[PRIVATE_NOTE]), SecurityTags)


class TestCatalogueValidation:
    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "documents.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_the_packaged_dev_catalogue_is_valid(self):
        from erp_agent.infrastructure.rag.document_catalogue import default_catalogue_path

        assert len(DocumentCatalogue.from_file(default_catalogue_path())) >= 3

    def test_an_unrecognised_schema_version_is_refused(self, tmp_path):
        path = self._write(tmp_path, "schema_version: 9\ndocuments: []\n")

        with pytest.raises(ConfigurationError, match="schema_version"):
            DocumentCatalogue.from_file(path)

    def test_a_team_document_naming_no_department_or_branch_is_refused(self, tmp_path):
        # It would be readable by nobody, which is a manifest bug rather than a
        # policy someone chose.
        path = self._write(
            tmp_path,
            "schema_version: 1\ndocuments:\n"
            '  - document_id: "d1"\n    title: "T"\n    access_level: team\n',
        )

        with pytest.raises(ConfigurationError, match="neither a department nor a branch"):
            DocumentCatalogue.from_file(path)

    def test_a_private_document_naming_no_owner_is_refused(self, tmp_path):
        path = self._write(
            tmp_path,
            "schema_version: 1\ndocuments:\n"
            '  - document_id: "d1"\n    title: "T"\n    access_level: private\n',
        )

        with pytest.raises(ConfigurationError, match="names no owner"):
            DocumentCatalogue.from_file(path)

    def test_a_duplicate_document_id_is_refused(self, tmp_path):
        path = self._write(
            tmp_path,
            "schema_version: 1\ndocuments:\n"
            '  - document_id: "d1"\n    title: "A"\n    access_level: public\n'
            '  - document_id: "d1"\n    title: "B"\n    access_level: public\n',
        )

        with pytest.raises(ConfigurationError, match="more than once"):
            DocumentCatalogue.from_file(path)

    def test_an_unknown_field_is_refused(self, tmp_path):
        path = self._write(
            tmp_path,
            "schema_version: 1\ndocuments:\n"
            '  - document_id: "d1"\n    title: "A"\n    access_level: public\n'
            '    departmnt: "typo"\n',
        )

        with pytest.raises(ConfigurationError, match="departmnt"):
            DocumentCatalogue.from_file(path)
