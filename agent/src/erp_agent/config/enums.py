"""Closed sets of configuration choices.

Every selection is an enum rather than a free string, so a typo fails at startup
with a list of valid values instead of falling through to a default and being
discovered when someone asks why the answers changed.

These are *config-level* names, deliberately distinct from any domain enum:
:mod:`erp_agent.config` imports no other layer, so where a concept exists on both
sides the composition root translates between them.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["AppEnv", "LLMProvider", "LogFormat"]


class AppEnv(StrEnum):
    """Deployment environment."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """How log events are rendered.

    ``console`` is human-readable for local work; ``json`` is what log
    aggregation can query, and is required anywhere real.
    """

    CONSOLE = "console"
    JSON = "json"


class LLMProvider(StrEnum):
    """Selectable answer-generation providers.

    Mirrors RAG's own enum, because the adapters behind it are RAG's. Ollama is
    absent deliberately: adding it means writing a new adapter against
    ``rag.domain.ports.LLMClient``, and a provider name that resolves to nothing
    is worse than one that is not offered (Plan.md gap G5 remains open).
    """

    OPENROUTER = "openrouter"
    GEMINI = "gemini"
