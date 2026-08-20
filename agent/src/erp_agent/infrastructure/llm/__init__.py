"""LLM adapters. Reuses RAG's port and its providers; adds no second abstraction."""

from __future__ import annotations

from erp_agent.infrastructure.llm.factory import build_llm_client

__all__ = ["build_llm_client"]
