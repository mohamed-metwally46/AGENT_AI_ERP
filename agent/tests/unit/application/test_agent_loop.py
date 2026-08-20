"""The bounded loop: what it executes, what it refuses, and where it stops."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from rag.domain.models import GenerationParams, LLMResponse
from rag.domain.models.tools import LLMMessage, ToolCall, ToolDefinition
from rag.domain.ports.llm_client import LLMClient

from erp_agent.application.agent.loop import AgentLoop
from erp_agent.application.agent.tool_registry import RegisteredTool, ToolRegistry
from erp_agent.application.agent.trace import REDACTED, ToolOutcome
from erp_agent.domain.errors import AgentLoopLimitError, DataSourceError, LLMUnavailableError
from erp_agent.domain.models.security_context import DataScope, SecurityContext

pytestmark = [pytest.mark.unit, pytest.mark.security]

CONTEXT = SecurityContext(
    user_id="1", employee_id="00000000-0000-0000-0000-000000000001", scope=DataScope.SELF
)
PARAMS = GenerationParams(temperature=0.0, max_output_tokens=512, timeout_s=30.0)


class ScriptedLLM(LLMClient):
    """Replays a fixed sequence of responses, recording what it was given."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[tuple[LLMMessage, ...], tuple[ToolDefinition, ...]]] = []

    @property
    def model_id(self) -> str:
        return "scripted"

    async def generate(self, prompt: Any, params: Any) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    def stream(self, prompt: Any, params: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def respond(
        self,
        messages: tuple[LLMMessage, ...],
        tools: tuple[ToolDefinition, ...],
        params: GenerationParams,
    ) -> LLMResponse:
        self.calls.append((messages, tools))
        if not self._responses:
            return _text("I have nothing further.")
        return self._responses.pop(0)


def _text(text: str) -> LLMResponse:
    return LLMResponse(model_id="scripted", text=text)


def _calls(*calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    return LLMResponse(
        model_id="scripted",
        text="",
        tool_calls=tuple(
            ToolCall(call_id=f"call-{index}", name=name, arguments=arguments)
            for index, (name, arguments) in enumerate(calls)
        ),
    )


class RecordingHandler:
    """A tool handler that records whether it was ever reached."""

    def __init__(self, result: Any = ("row",), fails_with: Exception | None = None) -> None:
        self.result = result
        self.fails_with = fails_with
        self.invocations: list[tuple[Any, dict[str, Any]]] = []

    async def __call__(self, context: Any = None, **arguments: Any) -> Any:
        self.invocations.append((context, arguments))
        if self.fails_with is not None:
            raise self.fails_with
        return self.result


def _registry(handler: RecordingHandler, *, requires_context: bool = True) -> ToolRegistry:
    return ToolRegistry(
        [
            RegisteredTool(
                definition=ToolDefinition(
                    name="count_employees",
                    description="Count employees.",
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "department": {"type": "string", "description": "Department name."}
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                ),
                handler=handler,
                requires_context=requires_context,
            )
        ]
    )


def _loop(llm: LLMClient, registry: ToolRegistry, **overrides: Any) -> AgentLoop:
    settings: dict[str, Any] = {
        "max_rounds": 3,
        "max_calls_per_round": 3,
        "max_total_calls": 6,
        "tool_timeout_s": 5.0,
    }
    settings.update(overrides)
    return AgentLoop(llm=llm, registry=registry, generation_params=PARAMS, **settings)


class TestHappyPath:
    async def test_a_direct_answer_needs_no_tool(self):
        handler = RecordingHandler()
        loop = _loop(ScriptedLLM(_text("Hello.")), _registry(handler))

        answer = await loop.answer("hi", CONTEXT)

        assert answer.text == "Hello."
        assert answer.trace.completed
        assert handler.invocations == []

    async def test_a_tool_result_reaches_the_next_round(self):
        llm = ScriptedLLM(_calls(("count_employees", {"department": "Sales"})), _text("Twelve."))
        handler = RecordingHandler(result=("a", "b"))

        answer = await _loop(llm, _registry(handler)).answer("how many in Sales?", CONTEXT)

        assert answer.text == "Twelve."
        assert answer.trace.tools_used == ("count_employees",)
        # The second call carries the tool result back to the model.
        second_round_messages = llm.calls[1][0]
        assert any(message.role == "tool" for message in second_round_messages)

    async def test_the_registry_is_advertised_to_the_model(self):
        llm = ScriptedLLM(_text("Hello."))
        registry = _registry(RecordingHandler())

        await _loop(llm, registry).answer("hi", CONTEXT)

        assert llm.calls[0][1] == registry.definitions


class TestTheSecurityContextIsThreaded:
    async def test_the_caller_context_reaches_the_handler_unchanged(self):
        handler = RecordingHandler()
        llm = ScriptedLLM(_calls(("count_employees", {"department": "Sales"})), _text("done"))

        await _loop(llm, _registry(handler)).answer("q", CONTEXT)

        assert handler.invocations[0][0] is CONTEXT

    async def test_a_contextless_tool_is_called_without_one(self):
        handler = RecordingHandler()
        llm = ScriptedLLM(_calls(("count_employees", {})), _text("done"))

        await _loop(llm, _registry(handler, requires_context=False)).answer("q", CONTEXT)

        assert handler.invocations[0][0] is None

    async def test_the_loop_never_constructs_a_security_context(self):
        # Read as a property of the source: the loop must have no code path
        # that builds one, or scope could be re-derived from model output.
        import inspect

        from erp_agent.application.agent import loop as loop_module

        source = inspect.getsource(loop_module)

        assert "SecurityContext(" not in source


class TestReservedArgumentsAreBlockedBeforeDispatch:
    @pytest.mark.parametrize(
        "hostile",
        [
            {"employee_id": "00000000-0000-0000-0000-0000000000ff"},
            {"scope": "organisation"},
            {"dept_id": "any"},
            {"limit": 100000},
            {"sql": "SELECT * FROM employees"},
            {"department": "Sales", "employee_id": "other"},
        ],
    )
    async def test_a_reserved_argument_stops_the_call_reaching_the_handler(self, hostile):
        handler = RecordingHandler()
        llm = ScriptedLLM(_calls(("count_employees", hostile)), _text("refused"))

        answer = await _loop(llm, _registry(handler)).answer("q", CONTEXT)

        assert handler.invocations == [], "the handler was reached despite a reserved argument"
        assert not answer.trace.reached_the_database
        (refusal,) = answer.trace.refusals
        assert refusal.outcome is ToolOutcome.REFUSED
        assert refusal.dispatched is False

    async def test_the_model_is_told_why_so_it_can_correct(self):
        # A silent drop teaches the model the call succeeded.
        llm = ScriptedLLM(_calls(("count_employees", {"scope": "organisation"})), _text("ok"))

        await _loop(llm, _registry(RecordingHandler())).answer("q", CONTEXT)

        tool_messages = [m for m in llm.calls[1][0] if m.role == "tool"]
        assert tool_messages
        assert "security layer" in tool_messages[0].content

    async def test_an_unregistered_tool_dispatches_nothing(self):
        handler = RecordingHandler()
        llm = ScriptedLLM(_calls(("execute_sql", {"sql": "SELECT 1"})), _text("no"))

        answer = await _loop(llm, _registry(handler)).answer("q", CONTEXT)

        assert handler.invocations == []
        assert not answer.trace.reached_the_database

    async def test_an_undeclared_argument_dispatches_nothing(self):
        handler = RecordingHandler()
        llm = ScriptedLLM(_calls(("count_employees", {"surprise": 1})), _text("no"))

        answer = await _loop(llm, _registry(handler)).answer("q", CONTEXT)

        assert handler.invocations == []
        assert answer.trace.refusals


class TestBoundsRaiseRatherThanTruncate:
    async def test_running_out_of_rounds_raises(self):
        # Every round asks for a tool and never answers.
        llm = ScriptedLLM(*[_calls(("count_employees", {})) for _ in range(5)])

        with pytest.raises(AgentLoopLimitError, match="within the permitted rounds"):
            await _loop(llm, _registry(RecordingHandler()), max_rounds=2).answer("q", CONTEXT)

    async def test_too_many_calls_in_one_round_raises(self):
        llm = ScriptedLLM(_calls(*[("count_employees", {})] * 4))

        with pytest.raises(AgentLoopLimitError, match="too many tools in one round"):
            await _loop(llm, _registry(RecordingHandler()), max_calls_per_round=2).answer(
                "q", CONTEXT
            )

    async def test_too_many_calls_overall_raises(self):
        llm = ScriptedLLM(
            *[_calls(("count_employees", {}), ("count_employees", {})) for _ in range(4)]
        )

        with pytest.raises(AgentLoopLimitError, match="maximum number of tool calls"):
            await _loop(
                llm, _registry(RecordingHandler()), max_calls_per_round=2, max_total_calls=3
            ).answer("q", CONTEXT)

    async def test_the_failure_carries_the_trace(self):
        # A bound hit with no answer must be visible, not silent.
        llm = ScriptedLLM(*[_calls(("count_employees", {})) for _ in range(5)])

        with pytest.raises(AgentLoopLimitError) as caught:
            await _loop(llm, _registry(RecordingHandler()), max_rounds=2).answer("q", CONTEXT)

        trace = caught.value.context["trace"]
        assert trace["completed"] is False
        assert trace["rounds"] == 2
        assert trace["tool_calls"] >= 1

    async def test_no_partial_answer_is_returned_instead(self):
        llm = ScriptedLLM(*[_calls(("count_employees", {})) for _ in range(5)])
        loop = _loop(llm, _registry(RecordingHandler()), max_rounds=1)

        with pytest.raises(AgentLoopLimitError):
            await loop.answer("q", CONTEXT)


class TestToolFailuresBecomeResultsNotCrashes:
    async def test_a_database_error_is_reported_to_the_model(self):
        handler = RecordingHandler(fails_with=DataSourceError("the query failed"))
        llm = ScriptedLLM(_calls(("count_employees", {})), _text("I could not check."))

        answer = await _loop(llm, _registry(handler)).answer("q", CONTEXT)

        assert answer.text == "I could not check."
        (invocation,) = answer.trace.invocations
        assert invocation.outcome is ToolOutcome.FAILED
        assert invocation.dispatched is True
        assert invocation.error_code == "DATA_SOURCE_ERROR"

    async def test_a_slow_tool_is_cancelled_and_reported(self):
        async def crawl(context: Any = None, **_: Any) -> Any:
            await asyncio.sleep(5)

        registry = ToolRegistry(
            [
                RegisteredTool(
                    definition=ToolDefinition(
                        name="count_employees",
                        description="Count employees.",
                        parameters_schema={
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        },
                    ),
                    handler=crawl,
                )
            ]
        )
        llm = ScriptedLLM(_calls(("count_employees", {})), _text("timed out"))

        answer = await _loop(llm, registry, tool_timeout_s=0.05).answer("q", CONTEXT)

        (invocation,) = answer.trace.invocations
        assert invocation.error_code == "TOOL_TIMEOUT"

    async def test_an_empty_model_response_is_an_error_not_an_answer(self):
        llm = ScriptedLLM(LLMResponse(model_id="scripted", text="   "))

        with pytest.raises(LLMUnavailableError, match="neither text nor a tool call"):
            await _loop(llm, _registry(RecordingHandler())).answer("q", CONTEXT)


class TestResultRendering:
    async def test_decimals_survive_as_exact_strings(self):
        from decimal import Decimal

        from erp_agent.domain.models.hr import LeaveBalance

        balance = LeaveBalance(
            employee_number="EMP-0001",
            leave_code="ANN",
            leave_name="Annual",
            fiscal_year=2026,
            opening_balance=Decimal("5.5"),
            accrued_ytd=Decimal("10.25"),
            used_ytd=Decimal("3"),
            pending_ytd=Decimal("0"),
        )
        llm = ScriptedLLM(_calls(("count_employees", {})), _text("done"))

        await _loop(llm, _registry(RecordingHandler(result=(balance,)))).answer("q", CONTEXT)

        content = next(m.content for m in llm.calls[1][0] if m.role == "tool")
        assert '"12.75"' in content, "the computed remaining balance is missing or lossy"
        assert "12.750000" not in content

    async def test_an_oversized_result_is_truncated_and_says_so(self):
        llm = ScriptedLLM(_calls(("count_employees", {})), _text("done"))
        handler = RecordingHandler(result=tuple(f"row-{i}" for i in range(5000)))

        await _loop(llm, _registry(handler), max_result_chars=200).answer("q", CONTEXT)

        content = next(m.content for m in llm.calls[1][0] if m.role == "tool")
        assert "truncated" in content


class TestTraceSanitisation:
    async def test_a_name_argument_is_redacted_in_the_trace(self):
        registry = ToolRegistry(
            [
                RegisteredTool(
                    definition=ToolDefinition(
                        name="count_employees",
                        description="Count employees.",
                        parameters_schema={
                            "type": "object",
                            "properties": {
                                "full_name": {"type": "string", "description": "A name."}
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                    ),
                    handler=RecordingHandler(),
                )
            ]
        )
        llm = ScriptedLLM(_calls(("count_employees", {"full_name": "Ahmed Hassan"})), _text("ok"))

        answer = await _loop(llm, registry).answer("q", CONTEXT)

        (invocation,) = answer.trace.invocations
        assert invocation.arguments["full_name"] == REDACTED
        assert "Ahmed" not in str(answer.trace.as_log_payload())

    async def test_a_safe_argument_keeps_its_value(self):
        llm = ScriptedLLM(_calls(("count_employees", {"department": "Sales"})), _text("ok"))

        answer = await _loop(llm, _registry(RecordingHandler())).answer("q", CONTEXT)

        assert answer.trace.invocations[0].arguments["department"] == "Sales"

    async def test_the_trace_records_rounds_and_completion(self):
        llm = ScriptedLLM(_calls(("count_employees", {})), _text("done"))

        answer = await _loop(llm, _registry(RecordingHandler())).answer("q", CONTEXT)

        assert answer.trace.rounds == 2
        assert answer.trace.completed
        assert answer.trace.reached_the_database
