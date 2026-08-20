"""Translating a scope into a retrieval filter RAG can apply."""

from __future__ import annotations

from pathlib import Path

import pytest
from rag.domain.models.filters import FieldFilter, FilterOperator
from rag.domain.models.metadata import FILTERABLE_FIELDS, MetadataField

from erp_agent.domain.errors import ConfigurationError
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue
from erp_agent.infrastructure.rag.security_filter import allowed_document_ids, retrieval_filter

pytestmark = [pytest.mark.unit, pytest.mark.security]

CATALOGUE = DocumentCatalogue.from_file(
    Path(__file__).resolve().parents[2] / "fixtures" / "documents.yaml"
)

PUBLIC = {
    "47a9adb9952e647654ade17ec397b664",
    "dc1dca3425232f7d80796aa557165d20",
    "fa698b8cfb8bf9319712cd7801032f48",
}
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


class TestTheBlockerIsReal:
    def test_rag_cannot_filter_on_any_security_field(self):
        # The finding this whole module is shaped around (Plan.md gap G3). If
        # this ever fails, Phase 6 has landed and the document-id indirection
        # below can be replaced with direct metadata filters.
        names = {field.value for field in FILTERABLE_FIELDS}

        assert not names & {"department", "branch", "owner_id", "access_level", "module"}

    def test_document_id_is_the_one_scoping_primitive_available(self):
        assert MetadataField.DOCUMENT_ID in FILTERABLE_FIELDS


class TestPublicDocuments:
    @pytest.mark.parametrize("scope", list(DataScope))
    def test_company_policy_is_readable_at_every_scope(self, scope):
        # An employee who cannot read the leave policy cannot check their own
        # leave.
        context = _context(
            scope, dept_id=ENGINEERING_DEPT, allowed_branch_ids=frozenset({CAIRO_BRANCH})
        )

        assert allowed_document_ids(context, CATALOGUE) >= PUBLIC


class TestTeamDocuments:
    def test_a_department_scoped_principal_reads_their_own_departments(self):
        context = _context(DataScope.DEPARTMENT, dept_id=ENGINEERING_DEPT)

        assert "doc-engineering-runbook" in allowed_document_ids(context, CATALOGUE)

    def test_a_department_scoped_principal_does_not_read_another_departments(self):
        context = _context(DataScope.DEPARTMENT, dept_id="00000000-0000-0000-0000-0000000000ff")

        assert "doc-engineering-runbook" not in allowed_document_ids(context, CATALOGUE)

    def test_a_branch_scoped_principal_reads_their_branch(self):
        context = _context(DataScope.BRANCH, allowed_branch_ids=frozenset({CAIRO_BRANCH}))

        assert "doc-cairo-handbook" in allowed_document_ids(context, CATALOGUE)

    def test_a_branch_scoped_principal_does_not_read_another_branch(self):
        context = _context(
            DataScope.BRANCH, allowed_branch_ids=frozenset({"00000000-0000-0000-0000-0000000000zz"})
        )

        assert "doc-cairo-handbook" not in allowed_document_ids(context, CATALOGUE)

    @pytest.mark.parametrize("scope", [DataScope.SELF, DataScope.TEAM])
    def test_narrow_scopes_reach_no_team_document(self, scope):
        # SELF and TEAM carry no departmental reach, so a document-level model
        # gives them nothing beyond public policy.
        context = _context(scope)
        permitted = allowed_document_ids(context, CATALOGUE)

        assert permitted == PUBLIC

    def test_organisation_scope_reads_every_team_document(self):
        permitted = allowed_document_ids(_context(DataScope.ORGANISATION), CATALOGUE)

        assert "doc-engineering-runbook" in permitted
        assert "doc-cairo-handbook" in permitted


class TestPrivateDocuments:
    def test_only_the_owner_reads_a_private_document(self):
        owner = _context(DataScope.SELF, employee_id=PRIVATE_OWNER)

        assert "doc-private-note" in allowed_document_ids(owner, CATALOGUE)

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_nobody_else_reads_it_at_any_scope(self, scope):
        # Not even organisation scope: private means private.
        other = _context(
            scope,
            employee_id="00000000-0000-0000-0000-00000000ffff",
            dept_id=ENGINEERING_DEPT,
            allowed_branch_ids=frozenset({CAIRO_BRANCH}),
        )

        assert "doc-private-note" not in allowed_document_ids(other, CATALOGUE)


class TestTheRenderedFilter:
    def test_it_is_a_document_id_membership_filter(self):
        expression = retrieval_filter(_context(DataScope.SELF), CATALOGUE)

        assert isinstance(expression, FieldFilter)
        assert expression.field is MetadataField.DOCUMENT_ID
        assert expression.operator is FilterOperator.IN
        assert set(expression.value) == PUBLIC

    def test_an_uncatalogued_document_is_never_in_the_filter(self):
        # Book.pdf and test.md are ingested but not catalogued. An untagged
        # document has no known audience, so it is visible to nobody.
        expression = retrieval_filter(_context(DataScope.ORGANISATION), CATALOGUE)

        assert isinstance(expression, FieldFilter)
        assert set(expression.value) <= set(CATALOGUE.records)

    def test_an_empty_permitted_set_refuses_to_render(self):
        # Returning no filter would mean "search everything" -- the same failure
        # the SQL predicate renderer refuses by never producing an empty clause.
        empty = DocumentCatalogue(records={}, source=None)

        with pytest.raises(ValueError, match="short-circuit"):
            retrieval_filter(_context(DataScope.ORGANISATION), empty)


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

    def test_a_team_document_naming_no_department_or_branch_is_refused(self):
        # It would be readable by nobody, which is a manifest bug rather than a
        # policy someone chose.
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "documents.yaml"
            path.write_text(
                "schema_version: 1\ndocuments:\n"
                '  - document_id: "d1"\n    title: "T"\n    access_level: team\n',
                encoding="utf-8",
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
