"""``GET /health`` -- is this process serving, and with what.

Unauthenticated on purpose: a readiness probe that needs a credential is a
readiness probe that fails for the wrong reason, and an orchestrator holding an
API token to check liveness is a credential in one more place.

It therefore reports only what is safe to tell anyone who can reach the port:
that the process is up, that the write barrier was verified before it served
anything, whether document retrieval is wired, and how many tools are
registered. No versions, no hostnames, no configuration, no counts of anything
in the database.

The write barrier is reported because a read-only claim nobody can check is not
a claim. It is not *re-verified* here -- that happened at start-up, and a
process whose barrier failed never got as far as binding a port.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from erp_agent.interfaces.api.schemas import HealthResponse

__all__ = ["router"]

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness and the safety properties verified at start-up",
)
async def health(request: Request) -> HealthResponse:
    """Report that the process is serving.

    Args:
        request: For the facts the app factory recorded at start-up.

    Returns:
        Status, the verified write barrier, whether retrieval is wired, and the
        registered tool count.
    """
    state = request.app.state
    return HealthResponse(
        status="ok",
        write_barrier="intact",
        knowledge="enabled" if state.knowledge_enabled else "disabled",
        tools=state.tool_count,
    )
