"""Ingesting documents with the audience their manifest entry declares.

Phase 6 moved permission scoping from a list this package maintained onto the
chunks themselves. That only works if something puts the tags there, and this is
that something.

**Why a manifest and not inference.** The ERP has no document ACL table outside
``project_documents`` (Plan.md gap S4), so there is nowhere to read a document's
audience from. The plan's answer is to *assign* it at ingestion and never infer
it, and the reason is worth stating: an audience guessed from a document's own
text is a guess, and a wrong guess is a disclosure. A handbook that mentions
"Engineering" is not thereby an Engineering document.

**Why untagged means invisible.** A file with no manifest entry is refused
rather than ingested untagged. Ingesting it would put chunks in the index that
no permission clause matches -- invisible, which is the safe outcome, but
invisible for a reason nobody can see. Refusing makes the omission a loud
failure at ingestion instead of a document that mysteriously never appears in
any search.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rag.domain.models import Document, SecurityTags
from rag.domain.policies import derive_document_id

from erp_agent.domain.errors import ConfigurationError
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue, DocumentRecord
from erp_agent.infrastructure.rag.security_filter import security_tags

__all__ = ["CatalogueIngestor", "IngestedDocument"]

#: Formats RAG can parse. Anything else in the directory is not a document this
#: corpus forgot to tag -- it is not a document.
_SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})


class _Ingest(Protocol):
    """RAG's ingestion use case, as much of it as this needs."""

    async def execute(
        self,
        filename: str,
        content: bytes,
        security: SecurityTags | None = None,
        *,
        force: bool = False,
    ) -> Document:
        """Ingest one file with its audience stamped on every chunk."""
        ...


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    """What one ingestion produced.

    Attributes:
        path: The file that was read.
        document_id: The identifier its chunks carry.
        title: The manifest's human-readable name, used in citations.
        access_level: The audience stamped onto every chunk.
        chunk_count: How many chunks were written.
    """

    path: Path
    document_id: str
    title: str
    access_level: str
    chunk_count: int


class CatalogueIngestor:
    """Ingests files, stamping each with the audience its manifest declares."""

    def __init__(
        self,
        *,
        ingest: _Ingest,
        catalogue: DocumentCatalogue,
        module: str | None = None,
    ) -> None:
        """Wire the ingestor.

        Args:
            ingest: RAG's ingestion use case.
            catalogue: The manifest deciding each document's audience.
            module: Functional area to stamp on everything this run ingests.
                One value per run rather than one per document, because a run
                ingests one corpus.
        """
        self._ingest = ingest
        self._catalogue = catalogue
        self._module = module

    def record_for(self, filename: str) -> DocumentRecord:
        """Find the manifest entry governing a file.

        Args:
            filename: The name the file will be ingested under. The document id
                is derived from it, by RAG's own rule rather than a copy of it.

        Returns:
            The manifest entry.

        Raises:
            ConfigurationError: If the file has no entry. Refused rather than
                ingested untagged -- see the module docstring.
        """
        document_id = derive_document_id(filename)
        record = self._catalogue.get(document_id)
        if record is None:
            raise ConfigurationError(
                f"{filename!r} (document_id {document_id}) has no entry in the "
                f"document manifest, so its audience is undeclared and it will "
                f"not be ingested",
                context={"filename": filename, "document_id": document_id},
            )
        return record

    async def ingest_file(self, path: Path) -> IngestedDocument:
        """Ingest one file under the audience its manifest entry declares.

        Args:
            path: The file to read.

        Returns:
            What was written.

        Raises:
            ConfigurationError: If the file is unreadable or uncatalogued.
        """
        record = self.record_for(path.name)

        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ConfigurationError(
                f"{path} could not be read: {exc.strerror}", context={"path": str(path)}
            ) from exc

        # `force`, always. This ingestor exists to put an audience on chunks,
        # and a document whose *content* has not changed is exactly the case
        # that needs it: without forcing, the ingestion use case sees an
        # identical hash, skips the write, and reports success while the chunks
        # keep whatever tags they had -- which for a pre-Phase-6 corpus is none,
        # leaving the document invisible to everyone. This was not theoretical:
        # the first run of the re-ingest silently skipped three of six files.
        document = await self._ingest.execute(
            path.name, content, security_tags(record, module=self._module), force=True
        )

        return IngestedDocument(
            path=path,
            document_id=document.document_id,
            title=record.title,
            access_level=record.access_level.value,
            chunk_count=document.chunk_count,
        )

    async def ingest_all(self, paths: Sequence[Path]) -> tuple[IngestedDocument, ...]:
        """Ingest several files, checking every manifest entry first.

        Every file is looked up before any is ingested. A run that would fail
        halfway leaves the index in a state where some documents carry the new
        tags and some do not, and "which half succeeded?" is a question nobody
        should have to answer during a re-ingest.

        Args:
            paths: The files to ingest.

        Returns:
            What was written, in the order given.

        Raises:
            ConfigurationError: If any file is uncatalogued or unreadable.
        """
        for path in paths:
            self.record_for(path.name)

        return tuple([await self.ingest_file(path) for path in paths])

    @staticmethod
    def documents_in(directory: Path) -> tuple[Path, ...]:
        """Every ingestible file in a directory, in a stable order.

        Args:
            directory: Where the corpus lives.

        Returns:
            The files, sorted by name so a re-ingest is reproducible.

        Raises:
            ConfigurationError: If the directory does not exist.
        """
        if not directory.is_dir():
            raise ConfigurationError(
                f"{directory} is not a directory", context={"path": str(directory)}
            )
        return tuple(
            sorted(
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES
            )
        )
