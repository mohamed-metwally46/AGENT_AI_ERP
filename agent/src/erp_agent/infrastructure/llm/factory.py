"""Building an LLM client, without inventing a second abstraction.

The port is :class:`rag.domain.ports.llm_client.LLMClient` and the adapters are
RAG's. This module only chooses between them from configuration and translates
their construction failures. There is no ``ErpLLMClient``, no wrapper interface
and no second tool-calling vocabulary (Plan.md section 7.1).

**One limitation, stated rather than worked around.** Tool calling is
OpenRouter-only. ``LLMClient.respond`` has a base implementation that raises, and
the Gemini adapter does not override it (Plan.md section 2.5, gap G6). Since the
agent *is* a tool loop, a Gemini deployment would start cleanly and fail on the
first real question. So the choice is refused in configuration instead, and this
module never has to decide what to do about it.

Making Gemini work would mean writing tool-call translation into RAG's Gemini
adapter -- a change to RAG, which this plan permits only in Phase 6 and only for
metadata. It is a blocker to be scheduled, not a gap to paper over here.
"""

from __future__ import annotations

from rag.domain.ports.llm_client import LLMClient

from erp_agent.domain.errors import ConfigurationError

__all__ = ["build_llm_client"]


def build_llm_client(
    *,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str,
) -> LLMClient:
    """Build the configured LLM client.

    Args:
        provider: Provider name, already validated by configuration.
        model: Provider-specific model identifier.
        api_key: Credential for the provider.
        base_url: Endpoint, for OpenAI-compatible providers. The request
            timeout is not passed here: it travels per-call on
            ``GenerationParams``, which is where RAG's adapter reads it.

    Returns:
        A client implementing RAG's port.

    Raises:
        ConfigurationError: If the credential is missing, the provider is not
            one that supports tool calling, or the provider SDK is not
            installed.
    """
    if provider != "openrouter":
        raise ConfigurationError(
            f"provider {provider!r} does not implement tool calling; the agent "
            f"requires 'openrouter' (Plan.md section 2.5, gap G6)",
            context={"variable": "ERP_LLM_PROVIDER", "provider": provider},
        )

    if not api_key or not api_key.strip():
        raise ConfigurationError(
            "ERP_LLM_API_KEY is required because ERP_LLM_PROVIDER=openrouter",
            context={"variable": "ERP_LLM_API_KEY"},
        )

    try:
        # Imported here, not at module scope, so a deployment that never builds
        # a client -- the diagnostics command, the unit suite -- does not need
        # the provider SDK installed.
        from rag.infrastructure.llm.openrouter_client import OpenRouterLLMClient
    except ImportError as exc:
        raise ConfigurationError(
            "the OpenRouter adapter needs the 'openai' package, which is not "
            "installed. The agent depends on RAG's domain only; installing RAG "
            "with its full dependency set brings the provider SDKs with it",
            context={"provider": provider},
        ) from exc

    client: LLMClient = OpenRouterLLMClient(api_key=api_key, base_url=base_url, model=model)
    return client
