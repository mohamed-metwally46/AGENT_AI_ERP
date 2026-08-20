"""A principal resolver backed by a YAML fixture.

Stands in for the Platform ``users`` / ``user_roles`` tables, which ``mock_hr``
does not have and which this phase must not create (Plan.md section 8.5, gap
S6). Phase 9 replaces this adapter with a SQL join; nothing above the port
changes.

Two properties make it safe to run:

*The file is validated once, at construction.* An unrecognised
``schema_version``, an unparseable row, a duplicate identity or an unknown field
raises before the process serves anything. Discovering a malformed principal
mid-request would mean discovering it as a failed authorization on a live
question.

*There is no fallback.* An unknown identity raises
:class:`PrincipalNotFoundError`. It never becomes ``SELF``, never becomes an
empty result, and never becomes a principal with no roles -- each of which would
convert an authentication failure into a successful, quietly-narrowed answer.

The pydantic row model is strictly internal. Callers see :class:`Principal` or
an exception, never a row type, so the fixture's shape cannot leak upward and
become something the API accidentally depends on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from erp_agent.domain.errors import ConfigurationError, PrincipalNotFoundError
from erp_agent.domain.models.principal import Principal
from erp_agent.domain.models.security_context import DataScope
from erp_agent.domain.ports.principal_resolver import PrincipalResolverPort

__all__ = ["SUPPORTED_SCHEMA_VERSION", "FixturePrincipalResolver", "default_fixture_path"]

#: The only fixture layout this loader understands. Bumped when the shape
#: changes, so an old file fails loudly instead of being read as if it were new.
SUPPORTED_SCHEMA_VERSION = 1

_PACKAGED_FIXTURE = Path(__file__).parent / "fixtures" / "principals.dev.yaml"


def default_fixture_path() -> Path:
    """The dev fixture shipped inside the package.

    Lives here rather than in :mod:`erp_agent.config` because config imports no
    other layer and therefore cannot know where an infrastructure file sits. The
    composition root asks for this when no path is configured.

    Returns:
        Absolute path to the packaged dev fixture.
    """
    return _PACKAGED_FIXTURE


class _PrincipalRow(BaseModel):
    """One fixture entry.

    ``extra="forbid"`` is deliberate: a misspelled ``role_names`` would
    otherwise be silently dropped, and the principal would resolve with no
    roles -- an under-privileged success, which is far harder to notice than a
    failure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    employee_id: str = Field(min_length=1)
    scope: DataScope
    dept_id: str | None = None
    branch_id: str | None = None
    allowed_branch_ids: tuple[str, ...] = ()
    role_names: tuple[str, ...] = ()

    def to_principal(self) -> Principal:
        """Convert to the domain type callers see."""
        return Principal(
            user_id=self.user_id,
            employee_id=self.employee_id,
            scope=self.scope,
            dept_id=self.dept_id,
            branch_id=self.branch_id,
            allowed_branch_ids=frozenset(self.allowed_branch_ids),
            role_names=frozenset(self.role_names),
        )


class FixturePrincipalResolver(PrincipalResolverPort):
    """Resolves principals from a validated YAML fixture, keyed by employee id.

    In fixture mode the authenticated identity *is* the employee id. Phase 9
    replaces that with a ``users.id -> users.employee_id`` join, which is why
    the port takes an opaque ``identity`` rather than an ``employee_id``.
    """

    def __init__(self, principals: dict[str, Principal], *, source: Path) -> None:
        """Hold an already-validated principal map.

        Prefer :meth:`from_file`, which does the loading and validation. This
        constructor exists so tests can build a resolver without a file.

        Args:
            principals: Identity to principal, already validated.
            source: Where they came from, for diagnostics.
        """
        self._principals = dict(principals)
        self._source = source

    @classmethod
    def from_file(cls, path: Path) -> FixturePrincipalResolver:
        """Load and validate a fixture file.

        Args:
            path: The YAML fixture to read.

        Returns:
            A resolver over the file's contents.

        Raises:
            ConfigurationError: If the file is missing, unparseable, carries an
                unrecognised ``schema_version``, contains a row that fails
                validation, or repeats an ``employee_id``. Every one of these is
                a deployment fault and stops start-up.
        """
        raw = cls._read(path)
        cls._check_schema_version(raw, path)

        entries = raw.get("principals")
        if not isinstance(entries, list) or not entries:
            raise ConfigurationError(
                f"principal fixture {path} must contain a non-empty 'principals' list",
                context={"path": str(path)},
            )

        principals: dict[str, Principal] = {}
        for index, entry in enumerate(entries):
            principal = cls._parse_row(entry, index=index, path=path)
            if principal.employee_id in principals:
                raise ConfigurationError(
                    f"principal fixture {path} defines employee_id "
                    f"{principal.employee_id!r} more than once; which grant wins "
                    f"would depend on file order",
                    context={"path": str(path), "employee_id": principal.employee_id},
                )
            principals[principal.employee_id] = principal

        return cls(principals, source=path)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        """Read and parse the YAML document."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"principal fixture {path} could not be read: {exc.strerror}",
                context={"path": str(path)},
            ) from exc

        try:
            # safe_load: a fixture must not be able to construct arbitrary
            # Python objects, whatever else is wrong with it.
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"principal fixture {path} is not valid YAML",
                context={"path": str(path)},
            ) from exc

        if not isinstance(document, dict):
            raise ConfigurationError(
                f"principal fixture {path} must be a mapping at the top level",
                context={"path": str(path)},
            )
        return document

    @staticmethod
    def _check_schema_version(document: dict[str, Any], path: Path) -> None:
        """Refuse a fixture whose layout this loader does not understand."""
        version = document.get("schema_version")
        if version != SUPPORTED_SCHEMA_VERSION:
            raise ConfigurationError(
                f"principal fixture {path} declares schema_version {version!r}; "
                f"this build understands only {SUPPORTED_SCHEMA_VERSION}",
                context={"path": str(path), "schema_version": repr(version)},
            )

    @staticmethod
    def _parse_row(entry: object, *, index: int, path: Path) -> Principal:
        """Validate one entry and convert it to a domain principal."""
        if not isinstance(entry, dict):
            raise ConfigurationError(
                f"principal fixture {path} entry {index} is not a mapping",
                context={"path": str(path), "index": index},
            )

        try:
            row = _PrincipalRow.model_validate(entry)
            return row.to_principal()
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'row'}: {error['msg']}"
                for error in exc.errors()
            )
            raise ConfigurationError(
                f"principal fixture {path} entry {index} is invalid: {problems}",
                context={"path": str(path), "index": index},
            ) from exc
        except ValueError as exc:
            # Raised by Principal/SecurityContext invariants -- for instance a
            # DEPARTMENT-scoped row with no dept_id, which would otherwise
            # produce a predicate matching nothing at question time.
            raise ConfigurationError(
                f"principal fixture {path} entry {index} is invalid: {exc}",
                context={"path": str(path), "index": index},
            ) from exc

    async def resolve(self, identity: str) -> Principal:
        """Resolve an identity against the fixture.

        Args:
            identity: The authenticated subject. In fixture mode this is the
                employee id.

        Returns:
            The principal.

        Raises:
            PrincipalNotFoundError: If the identity is not in the fixture. The
                identity is echoed because it is what the caller already
                supplied; nothing about other principals is disclosed, so the
                error cannot be used to enumerate the file.
        """
        principal = self._principals.get(identity.strip())
        if principal is None:
            raise PrincipalNotFoundError(
                "the authenticated identity does not resolve to a known principal",
                context={"identity": identity.strip()},
            )
        return principal

    @property
    def source(self) -> Path:
        """Where the principals were loaded from, for diagnostics."""
        return self._source

    def __len__(self) -> int:
        """How many principals were loaded."""
        return len(self._principals)
