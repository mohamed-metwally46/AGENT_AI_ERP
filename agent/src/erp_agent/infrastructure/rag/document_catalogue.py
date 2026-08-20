"""Which documents exist, and who may read them.

This catalogue exists because of two facts, neither of them chosen here.

``ChunkMetadata`` carries no department, branch, owner or access level (Plan.md
gap G3), so RAG cannot be *asked* which documents belong to Sales -- there is no
field to filter on. And the ERP has no document ACL table outside
``project_documents`` (gap S4), so there is nowhere in ``mock_hr`` to read the
answer from either.

So the mapping lives here, in a reviewed manifest, and is applied through the one
scoping primitive RAG does support: ``document_id``. Phase 6 moves these fields
onto the chunk payload and this file goes away.

**The limitation, stated rather than buried:** scoping is per *document*, not per
chunk. A document is visible or it is not. That is correct for a policy handbook
and wrong for a document that mixes departments, and nothing here detects the
second case -- Phase 6 is what fixes it.

``access_level`` mirrors ``project_documents.access_level`` (``private`` |
``team`` | ``public``) rather than inventing a parallel vocabulary, per Plan.md
section 7.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from erp_agent.domain.errors import ConfigurationError

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "AccessLevel",
    "DocumentCatalogue",
    "DocumentRecord",
    "default_catalogue_path",
]

#: The only manifest layout this loader understands.
SUPPORTED_SCHEMA_VERSION = 1

_PACKAGED_CATALOGUE = Path(__file__).parent / "fixtures" / "documents.dev.yaml"


def default_catalogue_path() -> Path:
    """The document manifest shipped inside the package."""
    return _PACKAGED_CATALOGUE


class AccessLevel(StrEnum):
    """How widely a document may be read.

    Mirrors ``project_documents.access_level``, which is the ERP-native
    vocabulary for exactly this question.
    """

    #: Readable by anyone authenticated. Company policy, handbooks, procedures.
    PUBLIC = "public"
    #: Readable within the owning department or branch.
    TEAM = "team"
    #: Readable only by the named owner.
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """One ingested document and the reach it was assigned.

    Attributes:
        document_id: The identifier RAG stores on every chunk. The join key
            between this manifest and the vector store.
        title: Human-readable name, used in citations.
        access_level: How widely it may be read.
        department: Owning department name, for ``team`` documents.
        branch: Owning branch name, for ``team`` documents.
        owner_employee_number: Owner, for ``private`` documents.
    """

    document_id: str
    title: str
    access_level: AccessLevel
    department: str | None = None
    branch: str | None = None
    owner_employee_number: str | None = None


class _DocumentRow(BaseModel):
    """One manifest entry. ``extra='forbid'`` so a typo fails loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    access_level: AccessLevel
    department: str | None = None
    branch: str | None = None
    owner_employee_number: str | None = None

    def to_record(self) -> DocumentRecord:
        """Convert to the type callers see."""
        return DocumentRecord(
            document_id=self.document_id,
            title=self.title,
            access_level=self.access_level,
            department=self.department,
            branch=self.branch,
            owner_employee_number=self.owner_employee_number,
        )


@dataclass(frozen=True, slots=True)
class DocumentCatalogue:
    """Every document the agent knows about, with its reach.

    Attributes:
        records: The documents, keyed by identifier.
        source: Where they were loaded from, for diagnostics.
    """

    records: dict[str, DocumentRecord] = field(default_factory=dict)
    source: Path | None = None

    def __len__(self) -> int:
        """How many documents are catalogued."""
        return len(self.records)

    def get(self, document_id: str) -> DocumentRecord | None:
        """Look up one document, or ``None`` if it is not catalogued."""
        return self.records.get(document_id)

    @classmethod
    def from_file(cls, path: Path) -> DocumentCatalogue:
        """Load and validate a manifest.

        Args:
            path: The YAML manifest to read.

        Returns:
            The catalogue.

        Raises:
            ConfigurationError: If the file is missing, unparseable, declares an
                unrecognised ``schema_version``, contains an invalid row, repeats
                a ``document_id``, or describes a reach it cannot express -- a
                ``team`` document with neither department nor branch would be
                readable by nobody, which is a manifest bug and not a policy.
        """
        document = _read_manifest(path)

        version = document.get("schema_version")
        if version != SUPPORTED_SCHEMA_VERSION:
            raise ConfigurationError(
                f"document manifest {path} declares schema_version {version!r}; "
                f"this build understands only {SUPPORTED_SCHEMA_VERSION}",
                context={"path": str(path)},
            )

        entries = document.get("documents")
        if not isinstance(entries, list):
            raise ConfigurationError(
                f"document manifest {path} must contain a 'documents' list",
                context={"path": str(path)},
            )

        records: dict[str, DocumentRecord] = {}
        for index, entry in enumerate(entries):
            record = _parse_row(entry, index=index, path=path)
            if record.document_id in records:
                raise ConfigurationError(
                    f"document manifest {path} defines document_id "
                    f"{record.document_id!r} more than once",
                    context={"path": str(path), "document_id": record.document_id},
                )
            records[record.document_id] = record

        return cls(records=records, source=path)


def _read_manifest(path: Path) -> dict[str, Any]:
    """Read and parse the manifest document."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"document manifest {path} could not be read: {exc.strerror}",
            context={"path": str(path)},
        ) from exc

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"document manifest {path} is not valid YAML", context={"path": str(path)}
        ) from exc

    if not isinstance(loaded, dict):
        raise ConfigurationError(
            f"document manifest {path} must be a mapping at the top level",
            context={"path": str(path)},
        )
    return loaded


def _parse_row(entry: object, *, index: int, path: Path) -> DocumentRecord:
    """Validate one entry and check its reach is expressible."""
    if not isinstance(entry, dict):
        raise ConfigurationError(
            f"document manifest {path} entry {index} is not a mapping",
            context={"path": str(path), "index": index},
        )

    try:
        record = _DocumentRow.model_validate(entry).to_record()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'row'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigurationError(
            f"document manifest {path} entry {index} is invalid: {problems}",
            context={"path": str(path), "index": index},
        ) from exc

    if record.access_level is AccessLevel.TEAM and not (record.department or record.branch):
        raise ConfigurationError(
            f"document manifest {path} entry {index} ({record.document_id}) is "
            f"'team' but names neither a department nor a branch, so nobody could "
            f"read it",
            context={"path": str(path), "index": index},
        )
    if record.access_level is AccessLevel.PRIVATE and not record.owner_employee_number:
        raise ConfigurationError(
            f"document manifest {path} entry {index} ({record.document_id}) is "
            f"'private' but names no owner, so nobody could read it",
            context={"path": str(path), "index": index},
        )
    return record
