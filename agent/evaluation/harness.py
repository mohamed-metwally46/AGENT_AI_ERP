"""Running the golden set.

The harness owns one thing: turning a case into a :class:`CaseResult`. It makes
no judgements -- the metric modules do that, from the observations recorded here
-- and it deliberately runs each case through the *real* agent loop, registry and
tools rather than a simplified path. An evaluation that exercises a
reimplementation of the system measures the reimplementation.

**Determinism comes from the model, not from the harness.** In ``replay`` mode
the model is a script carried in the case, so two runs over the same golden set
and the same database produce identical results. Everything else is real,
including the scope predicates and the vector store.

**A case that raises is recorded, not swallowed.** An exception during a run is
an observation like any other, and one that must fail the gate: a suite that
skips the cases it could not run reports on the ones that were easy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.domain.models import GenerationParams, LLMResponse
from rag.domain.models.tools import LLMMessage, ToolCall, ToolDefinition
from rag.domain.ports.llm_client import LLMClient

from erp_agent.application.agent.loop import AgentLoop
from erp_agent.core.container import Container
from erp_agent.domain.errors import ErpAgentError
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from evaluation.metrics.grounding import grounding_metrics
from evaluation.metrics.retrieval import retrieval_metrics
from evaluation.metrics.security import security_metrics
from evaluation.metrics.tools import tool_metrics
from evaluation.models import GoldenCase, GoldenSet
from evaluation.report import Report
from evaluation.results import CaseOutcome, CaseResult

__all__ = ["REPLAY", "Harness", "ReplayLLM", "run"]

#: The deterministic mode, and the one CI gates on.
REPLAY = "replay"
LIVE = "live"


class ReplayLLM(LLMClient):
    """A model that does what the golden case says it did.

    Not a mock of the model's *judgement* -- it has none, and the report says
    tool selection was not measured because of that. What it is, is a way to
    hold the model's output fixed so everything below it can be measured
    without a provider, a network, or a different answer on Tuesday.
    """

    def __init__(self, case: GoldenCase) -> None:
        """Script this client from one case."""
        self._case = case
        self._remaining = list(case.replay.calls)
        self.payloads: list[str] = []

    @property
    def model_id(self) -> str:
        """A name that cannot be mistaken for a real provider in a report."""
        return "replay"

    async def generate(self, prompt: Any, params: Any) -> LLMResponse:  # pragma: no cover
        """Unused: the agent only ever calls ``respond``."""
        raise NotImplementedError

    def stream(self, prompt: Any, params: Any) -> Any:  # pragma: no cover
        """Unused: the agent does not stream."""
        raise NotImplementedError

    async def respond(
        self,
        messages: tuple[LLMMessage, ...],
        tools: tuple[ToolDefinition, ...],
        params: GenerationParams,
    ) -> LLMResponse:
        """Replay the next scripted call, or deliver the scripted answer."""
        self.payloads = [message.content for message in messages if message.role == "tool"]

        if self._remaining:
            call = self._remaining.pop(0)
            return LLMResponse(
                model_id=self.model_id,
                text="",
                tool_calls=(
                    ToolCall(
                        call_id=f"{self._case.id}-{call.tool}",
                        name=call.tool,
                        arguments=dict(call.arguments),
                    ),
                ),
            )

        return LLMResponse(model_id=self.model_id, text=self._case.replay.answer)


@dataclass(frozen=True, slots=True)
class Harness:
    """Runs golden cases against a real container.

    Attributes:
        container: An opened container. The tools, the scope predicates and the
            database are the real ones.
        contexts: The security context each named principal resolves to,
            resolved once at start-up through the real authentication chain.
        mode: ``replay`` or ``live``.
        live_llm: The model, in live mode.
    """

    container: Container
    contexts: dict[str, SecurityContext]
    mode: str = REPLAY
    live_llm: LLMClient | None = None

    async def run_case(self, case: GoldenCase) -> CaseResult:
        """Run one case and record what happened.

        Args:
            case: What to ask, as whom, and what to expect.

        Returns:
            The observations. Never raises for a failure *inside* the run --
            that is recorded on the result, because a harness that propagates
            one case's failure loses the other fifty-nine.
        """
        context = self.contexts.get(case.principal)
        if context is None:
            return CaseResult(
                case=case,
                failure=CaseOutcome(
                    error_code="UNKNOWN_PRINCIPAL",
                    message=f"no dev principal is scoped {case.principal!r}",
                ),
            )

        llm = ReplayLLM(case) if self.mode == REPLAY else self.live_llm
        if llm is None:
            return CaseResult(
                case=case,
                failure=CaseOutcome("NO_MODEL", "live mode needs a configured model"),
            )

        loop = AgentLoop(
            llm=llm,
            registry=self.container.tool_registry,
            generation_params=self.container.generation_params,
            max_rounds=max(len(case.replay.calls) + 1, 2),
            max_total_calls=max(len(case.replay.calls), 1),
        )

        try:
            answer = await loop.answer(case.question, context)
        except ErpAgentError as exc:
            return CaseResult(
                case=case,
                payloads=tuple(getattr(llm, "payloads", [])),
                failure=CaseOutcome(exc.code, exc.message),
            )

        return CaseResult(
            case=case,
            answer=answer.text,
            sources=answer.sources,
            stripped_markers=answer.stripped_markers,
            trace=answer.trace,
            payloads=tuple(getattr(llm, "payloads", [])),
            control_row=await self._control_row(case),
        )

    async def _control_row(self, case: GoldenCase) -> dict[str, Any] | None:
        """Run the case's independent control query, if it has one.

        Through the same read-only data source as everything else, so a control
        query cannot do anything a tool could not -- ``SqlStatement`` refuses a
        write before it reaches the driver.
        """
        if case.control_sql is None:
            return None

        row = await self.container.data_source.fetch_one(SqlStatement(case.control_sql))
        return dict(row) if row is not None else None

    async def run(self, golden: GoldenSet) -> Report:
        """Run every case and aggregate the metrics.

        Args:
            golden: The cases.

        Returns:
            The report.
        """
        results = [await self.run_case(case) for case in golden.cases]
        measurable = self.mode != REPLAY

        return Report(
            mode=self.mode,
            counts=golden.counts,
            retrieval=retrieval_metrics(results),
            grounding=grounding_metrics(results),
            tools=tool_metrics(results, selection_measurable=measurable),
            security=security_metrics(results),
            failures=tuple(r.case.id for r in results if not r.answered),
        )


async def run(
    container: Container,
    golden: GoldenSet,
    *,
    contexts: dict[str, SecurityContext],
    mode: str = REPLAY,
    live_llm: LLMClient | None = None,
) -> Report:
    """Run a golden set against a container.

    Args:
        container: An opened container.
        golden: The cases.
        contexts: Security context per principal name.
        mode: ``replay`` or ``live``.
        live_llm: The model, in live mode.

    Returns:
        The report.
    """
    harness = Harness(container=container, contexts=contexts, mode=mode, live_llm=live_llm)
    return await harness.run(golden)
