"""Executable enforcement of the Dependency Rule.

``import-linter`` enforces the same contracts via ``make arch``. This suite runs
inside the ordinary ``pytest`` invocation, so a violation surfaces in the same
place as every other failure, and it reports the offending file and line rather
than a module pair.

Layering that is only documented is layering that erodes. These tests are the
difference between an architecture and a diagram.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

AGENT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = AGENT_ROOT.parent
SRC = AGENT_ROOT / "src"
PACKAGE_ROOT = SRC / "erp_agent"

#: Third-party distributions the domain is permitted to import: none.
_STDLIB = set(sys.stdlib_module_names)


@dataclass(frozen=True)
class Import:
    """One import statement found in the source tree."""

    module: str
    file: Path
    line: int

    @property
    def top_level(self) -> str:
        """The distribution or top-level package being imported."""
        return self.module.split(".", 1)[0]

    def __str__(self) -> str:
        """Render as a reviewer-friendly location."""
        return f"{self.file.relative_to(SRC)}:{self.line} imports {self.module!r}"


def _imports_of(layer: str) -> list[Import]:
    """Collect every module imported anywhere under ``erp_agent.<layer>``."""
    found: list[Import] = []
    for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
        # utf-8-sig so a stray byte-order mark cannot break the guard itself.
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(Import(alias.name, path, node.lineno) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.append(Import(node.module, path, node.lineno))
    return found


def _violations(layer: str, forbidden: set[str]) -> list[str]:
    """Return readable descriptions of every forbidden import in a layer."""
    return [
        str(imported)
        for imported in _imports_of(layer)
        if imported.top_level == "erp_agent"
        and any(
            imported.module == banned or imported.module.startswith(f"{banned}.")
            for banned in forbidden
        )
    ]


class TestDomainPurity:
    def test_domain_imports_no_third_party_package(self):
        # The security policies live in the domain. They must be reviewable and
        # testable without a database driver or a settings library in the way.
        offenders = [
            str(imported)
            for imported in _imports_of("domain")
            if imported.top_level not in _STDLIB and imported.top_level != "erp_agent"
        ]

        assert offenders == []

    def test_domain_imports_no_other_layer(self):
        assert (
            _violations(
                "domain",
                {
                    "erp_agent.application",
                    "erp_agent.interfaces",
                    "erp_agent.infrastructure",
                    "erp_agent.core",
                    "erp_agent.config",
                },
            )
            == []
        )

    def test_the_domain_does_not_import_the_rag_package(self):
        # RAG is consumed through an adapter from Phase 4. The domain must not
        # learn its types, or the two packages stop being separable.
        offenders = [
            str(imported) for imported in _imports_of("domain") if imported.top_level == "rag"
        ]

        assert offenders == []

    def test_the_domain_package_imports_cleanly_on_its_own(self):
        import importlib

        assert importlib.import_module("erp_agent.domain.models") is not None
        assert importlib.import_module("erp_agent.domain.ports") is not None
        assert importlib.import_module("erp_agent.domain.policies") is not None


class TestApplicationIsolation:
    def test_application_does_not_depend_on_infrastructure(self):
        assert (
            _violations(
                "application",
                {"erp_agent.infrastructure", "erp_agent.interfaces", "erp_agent.config"},
            )
            == []
        )

    def test_application_imports_no_vendor_sdk(self):
        banned = {
            "psycopg",
            "psycopg_pool",
            "qdrant_client",
            "openai",
            "google",
            "anthropic",
            "fastapi",
            "sqlalchemy",
        }
        offenders = [
            str(imported) for imported in _imports_of("application") if imported.top_level in banned
        ]

        assert offenders == []


class TestInfrastructureIsolation:
    def test_infrastructure_does_not_reach_back_into_orchestration(self):
        assert (
            _violations("infrastructure", {"erp_agent.application", "erp_agent.interfaces"}) == []
        )

    def test_infrastructure_does_not_read_configuration_directly(self):
        # Settings are injected as primitives by the composition root. An
        # adapter that reads config cannot be tested with two configurations in
        # one session.
        assert _violations("infrastructure", {"erp_agent.config"}) == []


class TestConfigIsALeaf:
    def test_config_imports_no_other_layer(self):
        assert (
            _violations(
                "config",
                {
                    "erp_agent.domain",
                    "erp_agent.application",
                    "erp_agent.interfaces",
                    "erp_agent.infrastructure",
                    "erp_agent.core",
                },
            )
            == []
        )


class TestGuardsAreRealisticallyWired:
    def test_the_scanner_finds_the_imports_that_do_exist(self):
        # A guard that silently scans nothing passes forever. This proves the
        # AST walk is actually reading source.
        assert any(
            imported.module.startswith("erp_agent.domain") for imported in _imports_of("core")
        )

    def test_the_scanner_would_catch_a_violation(self):
        # Proves _violations matches on prefix, not equality alone.
        assert _violations("core", {"erp_agent.domain"}) != []


#: The only RAG files Phase 6 is permitted to touch (Plan.md section 11, Phase 6
#: "Files"). RAG is modified exactly once in the whole roadmap, and the value of
#: saying so is lost if the change is allowed to spread afterwards.
PHASE_6_RAG_FILES = frozenset(
    {
        "RAG/src/rag/domain/models/metadata.py",
        "RAG/src/rag/domain/models/__init__.py",
        "RAG/src/rag/domain/policies/chunk_identity.py",
        "RAG/src/rag/domain/policies/__init__.py",
        "RAG/src/rag/application/use_cases/ingest_document.py",
        "RAG/src/rag/infrastructure/vector_store/qdrant_store.py",
    }
)


class TestTheRagChangeStayedWhereItWasAllowed:
    """Phase 6 is the one phase that modifies RAG, and only these files.

    Through Phase 5 this asserted ``git status -- RAG`` was empty. That guard
    expired the moment Phase 6 landed, so it was replaced rather than deleted:
    what matters now is not that RAG is untouched but that the touch stayed
    inside the six files the plan named, and that it stayed additive -- which
    ``tests/unit/infrastructure/test_rag_security_filter`` asserts field by
    field.

    Deleting the guard instead would have made "RAG is touched exactly once"
    an assertion nobody checks.
    """

    def _changed_rag_files(self) -> list[str] | None:
        if not (REPO_ROOT / ".git").exists():
            return None

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", "RAG"],  # noqa: S607
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return None

        if result.returncode != 0:  # pragma: no cover
            return None

        # Porcelain lines are "XY path"; a rename is "XY old -> new".
        return [
            line[3:].split(" -> ")[-1].strip().strip('"')
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def test_no_rag_file_outside_the_permitted_set_is_modified(self):
        changed = self._changed_rag_files()
        if changed is None:
            pytest.skip("git is unavailable or this is not a checkout")

        source = [path for path in changed if path.startswith("RAG/src/")]

        assert set(source) <= PHASE_6_RAG_FILES, (
            f"Phase 6 may only modify {sorted(PHASE_6_RAG_FILES)}; "
            f"unexpected: {sorted(set(source) - PHASE_6_RAG_FILES)}"
        )

    def test_no_rag_file_is_deleted(self):
        changed = self._changed_rag_files()
        if changed is None:
            pytest.skip("git is unavailable or this is not a checkout")

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", "RAG"],  # noqa: S607
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            pytest.skip("git is unavailable")

        deleted = [line for line in result.stdout.splitlines() if line.startswith(("D", " D"))]

        assert deleted == [], f"the RAG change must be additive; deleted: {deleted}"


class TestTheEvaluationLayerNeverRunsInTheRequestPath:
    """Section 5.2: the evaluation layer must not run in the request path.

    Guaranteed structurally -- `evaluation/` is outside `src/`, so the shipped
    package does not contain it -- and asserted here because "outside the
    package" is a property somebody could undo with one import.
    """

    def test_no_shipped_module_imports_the_evaluation_layer(self):
        offenders = [
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
            if "evaluation" in _imported_names(path)
        ]

        assert offenders == [], f"the request path must not import evaluation: {offenders}"

    def test_the_evaluation_layer_is_not_inside_the_package(self):
        assert not (PACKAGE_ROOT / "evaluation").exists()

    def test_the_golden_set_is_not_shipped_with_the_package(self):
        # Golden questions quote real employee numbers and real policy text.
        # They belong to the repository, not to a wheel somebody deploys.
        assert list(PACKAGE_ROOT.rglob("golden")) == []


def _imported_names(path: Path) -> set[str]:
    """Top-level module names a file imports."""
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:  # pragma: no cover
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class TestRuntimeCodeIsIndependentOfTheTestSuite:
    """`tests/` is pytest-owned. Nothing shipped may read from it.

    The dev principal fixture lives inside the package for this reason: a
    runtime path into `tests/` would make the test suite a deployment
    dependency, and would ship test principals to production.
    """

    def test_no_source_file_references_a_path_under_tests(self):
        offenders = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8-sig")
            for needle in ('"tests/', "'tests/", '"tests\\', "/tests/", "\\tests\\\\"):
                if needle in text:
                    offenders.append(f"{path.relative_to(SRC)} references {needle!r}")

        assert offenders == []

    def test_no_source_file_imports_the_test_package(self):
        offenders = [
            str(imported)
            for layer in ("domain", "application", "infrastructure", "core", "config")
            for imported in _imports_of(layer)
            if imported.top_level == "tests"
        ]

        assert offenders == []

    @pytest.mark.parametrize(
        "name", ["default_identity_fixture_path", "default_token_fixture_path"]
    )
    def test_the_packaged_dev_fixtures_are_inside_the_package(self, name):
        # They are data the wheel has to carry (pyproject `artifacts`). A fixture
        # resolved from outside the package works in a checkout and vanishes in
        # an installed one.
        import erp_agent.infrastructure.security as security

        fixture = getattr(security, name)()

        assert fixture.is_file()
        assert PACKAGE_ROOT in fixture.parents


class TestPointingAtADifferentDatabaseIsConfigurationOnly:
    """Phase 9 acceptance criterion 1, as a property of the source tree.

    "Switching from `mock_hr` to a read replica is configuration only" is a
    claim about where knowledge of the database lives. It holds if two things
    know and nothing else does: `config`, which reads the DSN, and
    `infrastructure/database`, which builds SQL with it. Anything else naming a
    table or holding a connection string would be a second place to edit during
    a cutover, and the one somebody forgets.

    The first version of these tests substring-matched table names across whole
    files and failed on the word "employees" appearing in a docstring. The
    claim worth making is narrower and exact: SQL is *constructed* in one place.
    """

    #: The one deliberate exception, and it is a diagnostics probe rather than a
    #: query the agent answers with: `--check` reads one count to prove the
    #: connection works. Named here so it is a decision rather than an
    #: oversight, and so a second exception has to be argued for.
    _PROBE = "__main__.py"

    def test_sql_is_constructed_only_in_the_data_layer(self):
        offenders = [
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
            if "infrastructure/database" not in path.as_posix()
            and path.name != self._PROBE
            and "SqlStatement(" in path.read_text(encoding="utf-8-sig")
        ]

        assert offenders == []

    def test_the_one_exception_is_a_read_and_nothing_more(self):
        # If the diagnostics probe ever grew a second statement, the exception
        # above would be covering something it was not argued for.
        source = (PACKAGE_ROOT / self._PROBE).read_text(encoding="utf-8-sig")

        assert source.count("SqlStatement(") == 1
        assert "SELECT count(*)" in source

    def test_only_config_holds_a_connection_string(self):
        # `config/validation.py` parses DSNs, so it names the scheme. Nothing
        # else should.
        offenders = [
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
            if "config/" not in path.relative_to(PACKAGE_ROOT).as_posix()
            and "postgresql://" in path.read_text(encoding="utf-8-sig")
        ]

        assert offenders == []

    def test_the_dsn_reaches_the_adapter_from_the_composition_root_only(self):
        # One path from configuration to the driver. A second would be a second
        # thing to change at cutover.
        source = (PACKAGE_ROOT / "core/container.py").read_text(encoding="utf-8-sig")

        assert "dsn=database.dsn.get_secret_value()" in source

    def test_the_schema_contract_is_the_only_place_tables_are_declared(self):
        # A drift check reads this list. A second list somewhere would drift
        # from it, and the check would verify the wrong one.
        from erp_agent.infrastructure.database.schema_contract import REQUIRED_TABLES

        assert len({table.name for table in REQUIRED_TABLES}) == len(REQUIRED_TABLES)
