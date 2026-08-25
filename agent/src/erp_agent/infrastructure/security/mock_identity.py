"""A development-only identity store, shaped like the one that replaces it.

Implements :class:`~erp_agent.domain.ports.principal_resolver.PrincipalResolverPort`
by composing three things the ERP will supply from four tables:

===========================  ==========================================
this adapter                 the ERP, after cutover
===========================  ==========================================
``roles:`` in the fixture    ``roles`` ⋈ ``role_permissions`` ⋈ ``permissions``
``users:`` in the fixture    ``users`` ⋈ ``user_roles``
:class:`OrgPlacementPort`    ``users.employee_id -> employees``
===========================  ==========================================

The rule that turns those into a grant is **not** here. It is
:meth:`~erp_agent.domain.models.role.RoleCatalogue.resolve`, in the domain, so
this adapter and the eventual ERP adapter cannot disagree about what holding two
roles means. All this module does is read a file, validate it hard, and hand the
domain the names.

Four properties make it safe to run:

*Everything is validated at load.* Unknown role, unknown resource, repeated
user, unrecognised ``schema_version``, misspelled key -- each raises
:class:`ConfigurationError` before the process serves anything.
``extra="forbid"`` matters more than it looks: a misspelled ``roles`` key would
otherwise be dropped and the user would resolve with no roles, which is an
under-privileged success rather than a failure.

*Placement is looked up, not written down.* The fixture names ``EMP-0001``;
:meth:`MockIdentityResolver.prepare` resolves the whole roster in one query at
start-up. An employee number the database does not have fails start-up. This is
what makes the file survive a re-seed -- and it is the same lookup the ERP
resolver performs.

*There is no fallback.* An unknown identity raises
:class:`PrincipalNotFoundError`; it never becomes ``SELF``, never becomes an
empty principal. An unprepared resolver raises rather than answering from a
placement nobody looked up.

*And it must not run in production.* The roster is committed to this repository
and its credentials are static. This module does not enforce that -- it cannot
know the environment -- :mod:`erp_agent.core.bootstrap` and
:mod:`erp_agent.core.container` do, independently of each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from erp_agent.domain.errors import ConfigurationError, PrincipalNotFoundError
from erp_agent.domain.models.authorization import (
    Action,
    Permission,
    PermissionLevel,
    PermissionSet,
    Resource,
)
from erp_agent.domain.models.principal import Principal
from erp_agent.domain.models.role import Role, RoleCatalogue, UnknownRoleError
from erp_agent.domain.models.security_context import DataScope
from erp_agent.domain.ports.org_placement import OrgPlacement, OrgPlacementPort
from erp_agent.domain.ports.principal_resolver import PrincipalResolverPort

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "MockIdentityResolver",
    "default_identity_fixture_path",
    "packaged_role_catalogue",
]

#: The only fixture layout this loader understands. Bumped from the Phase 2
#: principal fixture's ``1``, whose rows asserted a scope directly instead of
#: resolving one from roles -- so an old file fails loudly rather than being
#: read as if it were new.
SUPPORTED_SCHEMA_VERSION = 2

_PACKAGED_FIXTURE = Path(__file__).parent / "fixtures" / "identity.dev.yaml"


def default_identity_fixture_path() -> Path:
    """The dev identity fixture shipped inside the package.

    Lives here rather than in :mod:`erp_agent.config` because config imports no
    other layer and cannot know where an infrastructure file sits. The
    composition root asks for this when no path is configured.

    Returns:
        Absolute path to the packaged dev fixture.
    """
    return _PACKAGED_FIXTURE


def packaged_role_catalogue() -> RoleCatalogue:
    """The role catalogue from the packaged dev fixture.

    Exposed for tests and diagnostics that need to know what a role really
    grants. A test that instead granted a permission by hand would keep passing
    if the catalogue stopped granting it to any role anybody holds -- proving
    the policy correct about a permission nobody has.

    Returns:
        The catalogue.

    Raises:
        ConfigurationError: If the packaged fixture is malformed, which is a
            build fault rather than a deployment one.
    """
    return _load(default_identity_fixture_path()).catalogue


# --------------------------------------------------------------------------- #
# Fixture row models. Strictly internal: callers see domain types or an
# exception, never a row type, so the file's shape cannot leak upward and
# become something the API accidentally depends on.
# --------------------------------------------------------------------------- #


class _GrantRow(BaseModel):
    """One ``role_permissions`` row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource: Resource
    action: Action = Action.READ
    level: PermissionLevel = PermissionLevel.READ


class _RoleRow(BaseModel):
    """One ``roles`` row, with its grants inlined."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    scope: DataScope
    grants: tuple[_GrantRow, ...] = ()
    description: str = ""

    def to_role(self) -> Role:
        """Convert to the domain type."""
        return Role(
            name=self.name,
            scope=self.scope,
            grants=PermissionSet.of(
                (Permission(row.resource, row.action), row.level) for row in self.grants
            ),
            description=self.description,
        )


class _UserRow(BaseModel):
    """One ``users`` row joined to ``user_roles``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    employee_number: str = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    description: str = ""


class _Fixture:
    """A validated identity fixture, before placement is looked up."""

    __slots__ = ("catalogue", "source", "users")

    def __init__(
        self, *, catalogue: RoleCatalogue, users: Mapping[str, _UserRow], source: Path
    ) -> None:
        self.catalogue = catalogue
        self.users = dict(users)
        self.source = source


class MockIdentityResolver(PrincipalResolverPort):
    """Resolves identities against a dev fixture plus a live org lookup.

    In mock mode the authenticated identity is the ``user_id`` -- deliberately
    not the employee id, because ``users.id`` is what the ERP authenticates and
    a fixture keyed on an employee would not be replaceable by one.
    """

    def __init__(self, fixture: _Fixture, *, placements: OrgPlacementPort) -> None:
        """Hold a validated fixture and the placement lookup.

        Prefer :meth:`from_file`. This constructor is not part of the module's
        public surface -- ``_Fixture`` is internal -- and exists so
        :meth:`from_file` has somewhere to hand its result.

        Args:
            fixture: The already-validated fixture.
            placements: Where org placement is looked up.
        """
        self._fixture = fixture
        self._placements = placements
        self._principals: dict[str, Principal] | None = None

    @classmethod
    def from_file(cls, path: Path, *, placements: OrgPlacementPort) -> MockIdentityResolver:
        """Load and validate an identity fixture.

        Does no I/O beyond reading the file: placement is resolved by
        :meth:`prepare`, so a malformed fixture is reported before the database
        is involved and the two failures never get confused for each other.

        Args:
            path: The YAML fixture to read.
            placements: Where org placement will be looked up.

        Returns:
            An unprepared resolver.

        Raises:
            ConfigurationError: If the file is missing, unparseable, declares an
                unrecognised ``schema_version``, contains an invalid row, names
                a role the catalogue does not define, or repeats a ``user_id``.
        """
        return cls(_load(path), placements=placements)

    async def prepare(self) -> None:
        """Resolve every rostered employee number to its org placement.

        Called once, at start-up. Doing it here rather than per request is what
        keeps :class:`OrgPlacementPort` unreachable from a request: it is only
        ever asked about the fixed set of numbers this file names.

        Raises:
            ConfigurationError: If any employee number matches no employee. A
                roster entry that cannot be placed would otherwise resolve to a
                principal whose scope predicate matches nothing, discovered as
                somebody's empty answer.
            DataSourceUnavailableError: If the database is unreachable.
        """
        wanted = frozenset(row.employee_number for row in self._fixture.users.values())
        found = await self._placements.placements(wanted)

        if missing := sorted(wanted - set(found)):
            raise ConfigurationError(
                f"identity fixture {self._fixture.source} names employee number(s) "
                f"no employee has: {', '.join(missing)}",
                context={"path": str(self._fixture.source), "employee_numbers": tuple(missing)},
            )

        self._principals = {
            identity: _to_principal(row, found[row.employee_number], self._fixture)
            for identity, row in self._fixture.users.items()
        }

    async def resolve(self, identity: str) -> Principal:
        """Resolve an authenticated identity to its grant.

        Args:
            identity: The authenticated subject -- the ``user_id``.

        Returns:
            The principal, including the scope and permissions their roles
            resolve to.

        Raises:
            PrincipalNotFoundError: If the identity is not in the roster. The
                identity is echoed because it is what the caller already
                supplied; nothing about any other principal is disclosed, so
                the error cannot be used to enumerate the file.
            ConfigurationError: If :meth:`prepare` has not run. Deliberately not
                a lazy lookup: answering here would mean answering with a
                placement nobody verified at start-up.
        """
        if self._principals is None:
            raise ConfigurationError(
                "the mock identity resolver was not prepared; call prepare() during "
                "start-up so an unplaceable roster entry fails there rather than here",
                context={"path": str(self._fixture.source)},
            )

        principal = self._principals.get(identity.strip())
        if principal is None:
            raise PrincipalNotFoundError(
                "the authenticated identity does not resolve to a known principal",
                context={"identity": identity.strip()},
            )
        return principal

    @property
    def identities(self) -> tuple[str, ...]:
        """Every identity in the roster, sorted. For diagnostics and tests."""
        return tuple(sorted(self._fixture.users))

    @property
    def catalogue(self) -> RoleCatalogue:
        """The roles this deployment defines."""
        return self._fixture.catalogue

    @property
    def source(self) -> Path:
        """Where the roster was loaded from, for diagnostics."""
        return self._fixture.source

    def __len__(self) -> int:
        """How many identities were loaded."""
        return len(self._fixture.users)


def _to_principal(row: _UserRow, placement: OrgPlacement, fixture: _Fixture) -> Principal:
    """Combine a roster row, its placement, and the roles it holds.

    ``allowed_branch_ids`` is derived from the employee's own branch rather than
    declared. The real source is ``user_roles.branch_id`` (nullable meaning all
    branches), which does not exist here; deriving it means a ``BRANCH``-scoped
    principal reaches exactly one branch -- their own -- which is narrower than
    the ERP may eventually grant and therefore the safe direction to be wrong in.
    """
    grant = fixture.catalogue.resolve(row.roles)

    branches: frozenset[str] = frozenset()
    if grant.scope is DataScope.BRANCH and placement.branch_id:
        branches = frozenset({placement.branch_id})

    return Principal(
        user_id=row.user_id,
        employee_id=placement.employee_id,
        scope=grant.scope,
        dept_id=placement.dept_id,
        branch_id=placement.branch_id,
        allowed_branch_ids=branches,
        role_names=grant.role_names,
        permissions=grant.permissions,
    )


# --------------------------------------------------------------------------- #
# Loading and validation.
# --------------------------------------------------------------------------- #


def _load(path: Path) -> _Fixture:
    """Read, validate and index an identity fixture."""
    document = _read(path)

    version = document.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigurationError(
            f"identity fixture {path} declares schema_version {version!r}; "
            f"this build understands only {SUPPORTED_SCHEMA_VERSION}",
            context={"path": str(path), "schema_version": repr(version)},
        )

    catalogue = _parse_catalogue(document.get("roles"), path=path)
    users = _parse_users(document.get("users"), catalogue=catalogue, path=path)

    return _Fixture(catalogue=catalogue, users=users, source=path)


def _read(path: Path) -> dict[str, Any]:
    """Read and parse the YAML document."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"identity fixture {path} could not be read: {exc.strerror}",
            context={"path": str(path)},
        ) from exc

    try:
        # safe_load: a fixture must not be able to construct arbitrary Python
        # objects, whatever else is wrong with it.
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"identity fixture {path} is not valid YAML", context={"path": str(path)}
        ) from exc

    if not isinstance(loaded, dict):
        raise ConfigurationError(
            f"identity fixture {path} must be a mapping at the top level",
            context={"path": str(path)},
        )
    return loaded


def _parse_catalogue(entries: object, *, path: Path) -> RoleCatalogue:
    """Validate the ``roles`` block into a domain catalogue."""
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError(
            f"identity fixture {path} must contain a non-empty 'roles' list",
            context={"path": str(path)},
        )

    roles: list[Role] = []
    for index, entry in enumerate(entries):
        row = _validate(_RoleRow, entry, index=index, path=path, section="roles")
        try:
            roles.append(row.to_role())
        except ValueError as exc:
            raise ConfigurationError(
                f"identity fixture {path} role {index} is invalid: {exc}",
                context={"path": str(path), "index": index},
            ) from exc

    try:
        return RoleCatalogue(tuple(roles))
    except ValueError as exc:
        raise ConfigurationError(
            f"identity fixture {path} has an invalid role catalogue: {exc}",
            context={"path": str(path)},
        ) from exc


def _parse_users(entries: object, *, catalogue: RoleCatalogue, path: Path) -> dict[str, _UserRow]:
    """Validate the ``users`` block and check every role it names exists."""
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError(
            f"identity fixture {path} must contain a non-empty 'users' list",
            context={"path": str(path)},
        )

    users: dict[str, _UserRow] = {}
    for index, entry in enumerate(entries):
        row = _validate(_UserRow, entry, index=index, path=path, section="users")

        if row.user_id in users:
            raise ConfigurationError(
                f"identity fixture {path} defines user_id {row.user_id!r} more than "
                f"once; which roster entry wins would depend on file order",
                context={"path": str(path), "user_id": row.user_id},
            )

        # Resolved now rather than at request time, so a roster naming a role
        # nobody defined is a start-up failure instead of a principal who
        # quietly holds less than intended.
        try:
            catalogue.resolve(row.roles)
        except UnknownRoleError as exc:
            raise ConfigurationError(
                f"identity fixture {path} user {row.user_id!r} names a role the "
                f"catalogue does not define: {exc}",
                context={"path": str(path), "user_id": row.user_id},
            ) from exc

        users[row.user_id] = row

    return users


def _validate[RowT: BaseModel](
    model: type[RowT], entry: object, *, index: int, path: Path, section: str
) -> RowT:
    """Validate one fixture entry, reporting where it was."""
    if not isinstance(entry, dict):
        raise ConfigurationError(
            f"identity fixture {path} {section} entry {index} is not a mapping",
            context={"path": str(path), "index": index},
        )

    try:
        return model.model_validate(entry)
    except ValidationError as exc:
        problems = "; ".join(_problem(error) for error in exc.errors())
        raise ConfigurationError(
            f"identity fixture {path} {section} entry {index} is invalid: {problems}",
            context={"path": str(path), "index": index},
        ) from exc


def _problem(error: Any) -> str:
    """Render one validation error, naming the value that caused it.

    Pydantic's message for a rejected enum lists the permitted values but not
    the offending one, which leaves a fixture author searching. Echoing it is
    safe here: this file holds role and resource names, never credentials --
    those live in the token fixture, whose loader deliberately never echoes an
    input.
    """
    location = ".".join(str(part) for part in error["loc"]) or "row"
    message = f"{location}: {error['msg']}"

    given = error.get("input")
    if isinstance(given, str | int | float | bool):
        message = f"{message} (got {given!r})"
    return message
