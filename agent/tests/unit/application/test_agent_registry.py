"""The static allowlist, and what it refuses to advertise."""

from __future__ import annotations

from typing import Any

import pytest
from rag.domain.models.tools import ToolDefinition
from tests.unit.application.test_hr_tools import FakeDirectory

from erp_agent.application.agent.tool_registry import (
    RESERVED_ARGUMENT_NAMES,
    RegisteredTool,
    ToolRegistry,
    build_agent_registry,
    build_hr_registry,
)
from erp_agent.application.tools.hr import MVP_TOOL_NAMES
from erp_agent.domain.errors import ConfigurationError, ToolArgumentError
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.ports.knowledge import DocumentEvidence, KnowledgeBase

pytestmark = [pytest.mark.unit, pytest.mark.security]


async def _noop(*_: Any, **__: Any) -> None:
    return None


class FakeKnowledge(KnowledgeBase):
    """A knowledge port that is never reached: these tests never dispatch."""

    async def search(
        self, context: SecurityContext, *, query: str, top_k: int = 5
    ) -> DocumentEvidence:
        return DocumentEvidence(query=query, searched_document_count=0)


def _tool(name: str = "probe", **schema_overrides: Any) -> RegisteredTool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"thing": {"type": "string", "description": "A thing."}},
        "required": [],
        "additionalProperties": False,
    }
    schema.update(schema_overrides)
    return RegisteredTool(
        definition=ToolDefinition(name=name, description="A probe tool.", parameters_schema=schema),
        handler=_noop,
    )


class TestTheRegistryIsAnAllowlist:
    def test_it_holds_exactly_the_six_mvp_tools(self):
        registry = build_hr_registry(FakeDirectory())

        assert registry.names == MVP_TOOL_NAMES
        assert len(registry) == 6

    def test_there_is_no_way_to_register_a_tool_after_construction(self):
        # A registry that can grow at request time is not an allowlist.
        registry = build_hr_registry(FakeDirectory())

        assert not hasattr(registry, "register")
        assert not hasattr(registry, "add")

    def test_an_unregistered_name_is_refused_and_dispatches_nothing(self):
        registry = build_hr_registry(FakeDirectory())

        with pytest.raises(ToolArgumentError, match="no tool named"):
            registry.get("execute_sql")

    def test_the_advertised_definitions_match_the_registered_tools(self):
        registry = build_hr_registry(FakeDirectory())

        assert {d.name for d in registry.definitions} == registry.names

    def test_an_empty_registry_is_refused(self):
        with pytest.raises(ConfigurationError, match="at least one tool"):
            ToolRegistry([])

    def test_a_duplicate_name_is_refused(self):
        # Which implementation the model reaches would depend on list order.
        with pytest.raises(ConfigurationError, match="more than once"):
            ToolRegistry([_tool("probe"), _tool("probe")])


class TestSpecificationValidation:
    def test_an_undescribed_argument_is_refused(self):
        # An undescribed argument is one the model guesses the meaning of.
        with pytest.raises(ConfigurationError, match="undescribed"):
            ToolRegistry([_tool(properties={"thing": {"type": "string"}})])

    def test_a_blank_description_counts_as_undescribed(self):
        with pytest.raises(ConfigurationError, match="undescribed"):
            ToolRegistry([_tool(properties={"thing": {"type": "string", "description": ""}})])

    def test_requiring_an_undeclared_argument_is_refused(self):
        with pytest.raises(ConfigurationError, match="requires argument"):
            ToolRegistry([_tool(required=["nonexistent"])])

    def test_open_ended_schemas_are_refused(self):
        # additionalProperties=false is what stops a provider passing through
        # arguments the schema never declared.
        with pytest.raises(ConfigurationError, match="additionalProperties"):
            ToolRegistry([_tool(additionalProperties=True)])

    def test_a_missing_properties_object_is_refused(self):
        with pytest.raises(ConfigurationError, match="no 'properties'"):
            ToolRegistry(
                [
                    RegisteredTool(
                        definition=ToolDefinition(
                            name="probe",
                            description="x",
                            parameters_schema={"type": "object"},
                        ),
                        handler=_noop,
                    )
                ]
            )

    @pytest.mark.parametrize(
        "reserved",
        ["employee_id", "scope", "dept_id", "limit", "sql", "permissions", "permission_levels"],
    )
    def test_a_tool_declaring_a_reserved_argument_is_refused(self, reserved):
        # The strongest form of the guard: such a tool never reaches the model
        # at all, so the model is never even offered the shape.
        with pytest.raises(ConfigurationError, match="reserved argument"):
            ToolRegistry([_tool(properties={reserved: {"type": "string", "description": "x"}})])

    def test_a_non_callable_handler_is_refused(self):
        broken = RegisteredTool(
            definition=_tool().definition,
            handler="not a function",  # type: ignore[arg-type]
        )

        with pytest.raises(ConfigurationError, match="callable handler"):
            ToolRegistry([broken])


class TestNoneOfTheRealToolsExposeAScopeArgument:
    def test_no_registered_schema_declares_a_reserved_name(self):
        registry = build_hr_registry(FakeDirectory())

        for tool in registry:
            assert not tool.argument_names & RESERVED_ARGUMENT_NAMES, tool.name

    def test_every_registered_schema_is_closed(self):
        registry = build_hr_registry(FakeDirectory())

        for tool in registry:
            assert tool.definition.parameters_schema["additionalProperties"] is False

    def test_no_registered_tool_accepts_free_text_sql(self):
        registry = build_hr_registry(FakeDirectory())
        arguments = {name for tool in registry for name in tool.argument_names}

        assert not arguments & {"sql", "query", "table", "columns", "where", "filter"}

    def test_only_the_policy_tool_runs_without_a_security_context(self):
        # An entitlement rule is company policy. Everything else reads about
        # people and must be scoped.
        registry = build_hr_registry(FakeDirectory())

        contextless = {tool.name for tool in registry if not tool.requires_context}

        assert contextless == {"get_leave_policy"}


class TestTheDocumentToolJoinsOnTheSameTerms:
    """The seventh tool is registered like the other six, not beside them.

    ``search_documents`` is the one tool whose result the model may only quote,
    which makes it the obvious place for an exception to creep in -- a looser
    schema, an argument the guards do not cover, a second registry. These assert
    it went through the same door: the covering suites here and in
    ``tests/security/test_document_injection.py`` exercise it end to end, but
    only through the loop, and a registration bug would show up there as a
    confusing dispatch failure rather than as itself.
    """

    def _registry(self) -> ToolRegistry:
        return build_agent_registry(directory=FakeDirectory(), knowledge=FakeKnowledge())

    def test_it_is_the_seventh_tool_and_the_only_addition(self):
        registry = self._registry()

        assert registry.names == MVP_TOOL_NAMES | {"search_documents"}
        assert len(registry) == 7

    def test_the_six_structured_tools_are_unchanged_by_its_presence(self):
        hr_only = build_hr_registry(FakeDirectory())
        both = self._registry()

        for name in MVP_TOOL_NAMES:
            assert both.get(name).definition == hr_only.get(name).definition

    def test_its_schema_declares_no_reserved_name(self):
        tool = self._registry().get("search_documents")

        assert not tool.argument_names & RESERVED_ARGUMENT_NAMES

    @pytest.mark.parametrize("reserved", sorted(RESERVED_ARGUMENT_NAMES))
    def test_a_reserved_argument_is_refused_at_call_time(self, reserved):
        # The same guard the injection suite relies on, asserted directly on the
        # registered tool rather than inferred from a refusal in the trace.
        tool = self._registry().get("search_documents")

        with pytest.raises(ToolArgumentError, match="supplied by the security layer"):
            tool.reject_reserved({reserved: "anything"})

    def test_its_schema_is_closed_like_the_others(self):
        tool = self._registry().get("search_documents")

        assert tool.definition.parameters_schema["additionalProperties"] is False
        with pytest.raises(ToolArgumentError, match="does not accept"):
            tool.reject_unknown({"collection": "everything"})

    def test_it_requires_a_security_context(self):
        # Only `get_leave_policy` runs without one, and that is company policy
        # rather than anything about a person. Documents are scoped.
        assert self._registry().get("search_documents").requires_context

    def test_it_accepts_a_search_string_but_still_no_sql(self):
        # `query` is a search string here, and was over-reserved in Phase 3. The
        # names that would let a model *shape a database read* stay refused.
        arguments = {name for tool in self._registry() for name in tool.argument_names}

        assert "query" in arguments
        assert not arguments & {"sql", "table", "columns", "where", "filter"}


class TestArgumentGuards:
    @pytest.mark.parametrize("reserved", sorted(RESERVED_ARGUMENT_NAMES))
    def test_every_reserved_name_is_refused_at_call_time(self, reserved):
        tool = _tool()

        with pytest.raises(ToolArgumentError, match="supplied by the security layer"):
            tool.reject_reserved({reserved: "anything"})

    def test_ordinary_arguments_pass_the_reserved_guard(self):
        _tool().reject_reserved({"thing": "value"})

    def test_an_undeclared_argument_is_refused(self):
        with pytest.raises(ToolArgumentError, match="does not accept"):
            _tool().reject_unknown({"surprise": 1})

    def test_the_refusal_names_the_argument_but_not_its_value(self):
        # Tool arguments come from model output and may quote anything a user
        # typed.
        with pytest.raises(ToolArgumentError) as caught:
            _tool().reject_reserved({"employee_id": "00000000-secret-value"})

        assert "employee_id" in caught.value.message
        assert "secret-value" not in caught.value.message
        assert "secret-value" not in str(caught.value.context)
