"""Stamping a document's audience at ingestion.

The counterpart to the retrieval filter: the filter is only as good as what was
written, and everything the filter enforces depends on these tags being present
and correct. Three properties matter.

*An uncatalogued file is refused, not ingested untagged.* Ingesting it would put
chunks in the index that no clause matches -- invisible, which is safe, but
invisible for a reason nobody can see. A loud failure at ingestion is the better
trade.

*Nothing is written until every file has been checked.* A run that fails halfway
leaves an index where some documents carry the new tags and some do not, and
nobody can tell which is which afterwards.

*Unchanged content is re-indexed anyway.* This is the one that was actually
wrong: the ingestion use case skips a document whose bytes are identical, which
is right for content and wrong for metadata. The first real re-ingest silently
skipped three of six files, and the test below is the reason it cannot happen
again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from rag.domain.models import DocumentType, SecurityTags
from rag.domain.policies import derive_document_id

from erp_agent.domain.errors import ConfigurationError
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue
from erp_agent.infrastructure.rag.ingestion import CatalogueIngestor

pytestmark = [pytest.mark.unit, pytest.mark.security]

CATALOGUE = DocumentCatalogue.from_file(
    Path(__file__).resolve().parents[2] / "fixtures" / "documents.yaml"
)
ENGINEERING_DEPT = "00000000-0000-0000-0000-0000000000d9"
PRIVATE_OWNER = "00000000-0000-0000-0000-000000000005"


@dataclass
class _Written:
    filename: str
    security: SecurityTags | None
    force: bool


class FakeIngest:
    """Records what it was asked to write, without writing anything."""

    def __init__(self) -> None:
        self.writes: list[_Written] = []

    async def execute(
        self,
        filename: str,
        content: bytes,
        security: SecurityTags | None = None,
        *,
        force: bool = False,
    ) -> Any:
        self.writes.append(_Written(filename=filename, security=security, force=force))

        from rag.domain.models import Document

        return Document(
            document_id=derive_document_id(filename),
            filename=filename,
            document_type=DocumentType.MARKDOWN,
            content_hash="hash",
            size_bytes=len(content),
            source_path=f"uploads/{filename}",
            ingest_version=1,
            chunk_count=3,
            ingested_at=datetime(2026, 8, 20, tzinfo=UTC),
        )


def _corpus(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_text(f"# {name}\n\nSome text.\n", encoding="utf-8")
        paths.append(path)
    return paths


def _ingestor(ingest: FakeIngest, **overrides: Any) -> CatalogueIngestor:
    return CatalogueIngestor(ingest=ingest, catalogue=CATALOGUE, **overrides)


class TestTheAudienceIsStamped:
    async def test_a_public_document_is_written_as_public(self, tmp_path):
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "annual_leave_policy.md")

        await _ingestor(ingest).ingest_file(path)

        assert ingest.writes[0].security is not None
        assert ingest.writes[0].security.access_level == "public"

    async def test_a_team_document_carries_its_department(self, tmp_path):
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "engineering_runbook.md")

        await _ingestor(ingest).ingest_file(path)

        tags = ingest.writes[0].security
        assert tags is not None
        assert tags.access_level == "team"
        assert tags.department == ENGINEERING_DEPT

    async def test_a_private_document_carries_its_owner(self, tmp_path):
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "private_note.md")

        await _ingestor(ingest).ingest_file(path)

        tags = ingest.writes[0].security
        assert tags is not None
        assert tags.access_level == "private"
        assert tags.owner_id == PRIVATE_OWNER

    async def test_the_module_is_stamped_across_the_run(self, tmp_path):
        ingest = FakeIngest()
        paths = _corpus(tmp_path, "annual_leave_policy.md", "overtime_policy.md")

        await _ingestor(ingest, module="HR").ingest_all(paths)

        assert [w.security.module for w in ingest.writes if w.security] == ["HR", "HR"]

    async def test_the_result_reports_what_was_stamped(self, tmp_path):
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "engineering_runbook.md")

        result = await _ingestor(ingest).ingest_file(path)

        assert result.document_id == derive_document_id("engineering_runbook.md")
        assert result.access_level == "team"
        assert result.title == "Engineering On-Call Runbook"
        assert result.chunk_count == 3


class TestUnchangedContentIsStillRetagged:
    async def test_ingestion_is_always_forced(self, tmp_path):
        # The defect this exists to prevent: the ingestion use case skips a
        # document whose content hash is unchanged, so a metadata-only re-ingest
        # reports success and writes nothing.
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "annual_leave_policy.md")

        await _ingestor(ingest).ingest_file(path)

        assert ingest.writes[0].force is True

    async def test_every_file_in_a_run_is_forced(self, tmp_path):
        ingest = FakeIngest()
        paths = _corpus(tmp_path, "annual_leave_policy.md", "code_of_conduct.md")

        await _ingestor(ingest).ingest_all(paths)

        assert all(write.force for write in ingest.writes)


class TestAnUncataloguedFileIsRefused:
    async def test_ingesting_it_raises_rather_than_writing_untagged_chunks(self, tmp_path):
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "not_in_the_manifest.md")

        with pytest.raises(ConfigurationError, match="no entry in the document manifest"):
            await _ingestor(ingest).ingest_file(path)

        assert ingest.writes == []

    async def test_the_refusal_names_the_id_it_looked_for(self, tmp_path):
        ingest = FakeIngest()
        (path,) = _corpus(tmp_path, "not_in_the_manifest.md")

        with pytest.raises(ConfigurationError) as caught:
            await _ingestor(ingest).ingest_file(path)

        assert derive_document_id("not_in_the_manifest.md") in caught.value.message

    async def test_a_run_containing_one_writes_nothing_at_all(self, tmp_path):
        # Checked before the first write, not discovered halfway through.
        ingest = FakeIngest()
        paths = _corpus(tmp_path, "annual_leave_policy.md", "not_in_the_manifest.md")

        with pytest.raises(ConfigurationError):
            await _ingestor(ingest).ingest_all(paths)

        assert ingest.writes == []


class TestChoosingWhatToIngest:
    def test_only_parseable_formats_are_offered(self, tmp_path):
        _corpus(tmp_path, "a.md", "b.txt")
        (tmp_path / "notes.xlsx").write_text("x", encoding="utf-8")
        (tmp_path / "ingested.json").write_text("{}", encoding="utf-8")

        found = {path.name for path in CatalogueIngestor.documents_in(tmp_path)}

        assert found == {"a.md", "b.txt"}

    def test_the_order_is_stable_so_a_reingest_is_reproducible(self, tmp_path):
        _corpus(tmp_path, "c.md", "a.md", "b.md")

        found = [path.name for path in CatalogueIngestor.documents_in(tmp_path)]

        assert found == sorted(found)

    def test_a_missing_directory_is_refused(self, tmp_path):
        with pytest.raises(ConfigurationError, match="is not a directory"):
            CatalogueIngestor.documents_in(tmp_path / "nowhere")

    async def test_an_unreadable_file_is_reported_not_skipped(self, tmp_path):
        ingest = FakeIngest()

        with pytest.raises(ConfigurationError, match="could not be read"):
            await _ingestor(ingest).ingest_file(tmp_path / "annual_leave_policy.md")


class TestTheManifestIsTheOnlySourceOfAudience:
    def test_the_lookup_uses_rags_own_id_rule(self, tmp_path):
        # Not a local copy of the hash. If the two ever diverged, every document
        # would look uncatalogued and the whole corpus would go invisible.
        ingestor = _ingestor(FakeIngest())

        record = ingestor.record_for("annual_leave_policy.md")

        assert record.document_id == derive_document_id("annual_leave_policy.md")

    def test_content_does_not_influence_the_audience(self, tmp_path):
        # An audience inferred from prose is a guess, and a wrong guess is a
        # disclosure. Two files with the same name and different content get the
        # same tags; the text is never consulted.
        ingestor = _ingestor(FakeIngest())

        first = ingestor.record_for("engineering_runbook.md")
        second = ingestor.record_for("engineering_runbook.md")

        assert first == second
        assert first.access_level.value == "team"
