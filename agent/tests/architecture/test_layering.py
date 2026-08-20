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


class TestRagIsUntouched:
    """Task 1.1 acceptance criterion 5: ``git diff --stat RAG/`` is empty.

    Phases 1 to 5 require zero changes to RAG (Plan.md section 7.3). The agent
    owns its own tool registry and loop precisely so that stays true.
    """

    def test_the_rag_package_has_no_uncommitted_changes(self):
        if not (REPO_ROOT / ".git").exists():
            pytest.skip("not a git checkout")

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", "RAG"],  # noqa: S607
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            pytest.skip(f"git is unavailable: {exc}")

        if result.returncode != 0:  # pragma: no cover
            pytest.skip(f"git status failed: {result.stderr.strip()}")

        changed = [line for line in result.stdout.splitlines() if line.strip()]
        assert changed == [], "RAG/ must not be modified before Phase 6"


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

    def test_the_packaged_dev_fixture_is_inside_the_package(self):
        from erp_agent.infrastructure.security import default_fixture_path

        fixture = default_fixture_path()

        assert fixture.is_file()
        assert PACKAGE_ROOT in fixture.parents
