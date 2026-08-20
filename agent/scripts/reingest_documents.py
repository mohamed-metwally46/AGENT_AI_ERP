"""Re-ingest a document corpus with Phase 6 security metadata.

Phase 6 moved permission scoping onto the chunks (Plan.md section 7.3). Chunks
written before that carry no ``access_level``, and the retrieval filter pins
``access_level`` in every clause, so those chunks are readable by nobody until
they are written again. That is the intended failure mode -- invisible rather
than public -- and this script is the fix for it.

Usage, from the ``agent`` directory::

    uv run python scripts/reingest_documents.py
        --manifest tests/fixtures/documents.yaml
        --directory tests/fixtures/documents
        --module HR

Add ``--record tests/fixtures/documents/ingested.json`` to write out the
resulting document ids, which the integration suite reads.

Reproducible by construction: chunk ids are derived from content and position,
so running it twice writes the same points rather than duplicating them, and a
document whose content has not changed is skipped by the ingestion use case
itself. Files are ingested in sorted order, and any file missing a manifest
entry aborts the run *before* anything is written -- a half-tagged index is
worse than an untagged one, because nobody can tell which half is which.

RAG is configured from its own environment, so this bootstraps RAG's container
from the ``RAG`` directory. That is a consequence of the composition root not
yet wiring retrieval (carried forward from Phase 5), not a preference.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENT_ROOT.parent
sys.path.insert(0, str(_AGENT_ROOT / "src"))

from erp_agent.core.event_loop import loop_factory  # noqa: E402
from erp_agent.domain.errors import ConfigurationError  # noqa: E402
from erp_agent.infrastructure.rag.document_catalogue import DocumentCatalogue  # noqa: E402
from erp_agent.infrastructure.rag.ingestion import CatalogueIngestor  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--manifest", type=Path, required=True, help="the document manifest")
    parser.add_argument("--directory", type=Path, required=True, help="where the files live")
    parser.add_argument("--module", default=None, help="functional area to stamp, e.g. HR")
    parser.add_argument(
        "--record",
        type=Path,
        default=None,
        help="write {filename: document_id} here after a successful run",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    # Resolved before anything changes directory: RAG is configured relative to
    # its own root, so the chdir below would otherwise reinterpret every
    # relative path the caller gave us.
    manifest = Path(args.manifest).resolve()
    directory = Path(args.directory).resolve()
    record_to = None if args.record is None else Path(args.record).resolve()

    catalogue = DocumentCatalogue.from_file(manifest)
    paths = CatalogueIngestor.documents_in(directory)
    if not paths:
        print(f"no ingestible files in {directory}")
        return 1

    # RAG reads its configuration relative to its own root.
    previous = Path.cwd()
    os.chdir(_REPO_ROOT / "RAG")
    try:
        from rag.core.bootstrap import bootstrap

        container = bootstrap()
        await container.prepare_store()
        ingestor = CatalogueIngestor(
            ingest=container.ingest_document,
            catalogue=catalogue,
            module=args.module,
        )
        results = await ingestor.ingest_all(paths)
    finally:
        os.chdir(previous)

    for result in results:
        print(
            f"{result.path.name:<28} {result.document_id}  "
            f"{result.access_level:<8} {result.chunk_count:>3} chunks  {result.title}"
        )

    if record_to is not None:
        record_to.write_text(
            json.dumps({r.path.name: r.document_id for r in results}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        print(f"\nrecorded {len(results)} document ids in {record_to}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Re-ingest the corpus, or explain why it could not."""
    args = _parse_args(argv)
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(_run(args))
    except ConfigurationError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
