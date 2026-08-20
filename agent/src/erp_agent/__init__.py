"""Read-only ERP question-answering agent.

A standalone package beside the Document RAG (Plan.md section 10). It answers
questions about ERP data and ERP documents, and it is incapable of changing
either: the connection it holds is read-only at the database, at the session, in
the code paths that exist, and in the tools it exposes.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
