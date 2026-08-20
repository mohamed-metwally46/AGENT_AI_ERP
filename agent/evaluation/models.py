"""The golden set: what a question is, and what a correct answer looks like.

A golden case is *data*, validated on load, so a malformed expectation fails
before a run rather than producing a metric nobody can interpret. ``extra
="forbid"`` throughout: a misspelled ``expect_tools`` would otherwise be dropped
silently and the case would pass by expecting nothing.

**Two run modes, and the difference is the point.**

``replay`` carries the model's tool calls in the case itself. The run is
deterministic, needs no provider, and measures everything that is a property of
*this system*: whether the arguments were valid, whether the scope predicate
held, whether a citation resolved, whether a restricted value escaped. It cannot
measure tool selection, because the selection is written down -- and the report
says so rather than reporting 100%.

``live`` calls the configured model. It measures selection and parameter
extraction on top, and it is not reproducible, which is why it is not the gate.

Splitting them is what lets CI have a gate that means something. A suite that
mixed the two would fail on a model's bad day and be silenced, and the
authorization checks -- which are pass/fail, and which no model influences --
would be silenced with it.

**``control_sql`` is human-authored SQL, and that is not a contradiction of
"the model never writes SQL".** The rule exists because a model choosing its own
query chooses its own scope. A control query written by a person, reviewed in
this repository, and executed through the same read-only barrier as everything
else is the independent check that the tool's own query is right. It is the
opposite of the thing that is forbidden.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "GoldenCase",
    "GoldenSet",
    "QuestionClass",
    "ReplayScript",
    "ScriptedCall",
    "load_golden_set",
]

#: The only golden-set layout this loader understands.
SUPPORTED_SCHEMA_VERSION = 1


class GoldenSetError(Exception):
    """A golden file is missing, malformed, or self-contradictory."""


class QuestionClass(StrEnum):
    """Which of Plan.md section 12's metric groups a case belongs to."""

    DOCUMENT = "document"
    STRUCTURED = "structured"
    HYBRID = "hybrid"
    SECURITY = "security"


class ScriptedCall(BaseModel):
    """One tool call the model is scripted to make in ``replay`` mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ReplayScript(BaseModel):
    """What the model does, when the model is not consulted.

    Attributes:
        calls: Tool calls, in order, one per round.
        answer: The final answer text. Written to cite the markers the calls
            produce, so citation validity is a real measurement rather than a
            tautology -- a script that cited nothing would score perfectly and
            prove nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    calls: tuple[ScriptedCall, ...] = ()
    answer: str = Field(min_length=1)


class GoldenCase(BaseModel):
    """One question and everything a correct answer must satisfy.

    Attributes:
        id: Stable identifier. Appears in the report and in any regression
            diff, so it must not be renamed casually.
        question: What is asked.
        question_class: Which metric group this counts towards.
        principal: Which dev principal asks it, by scope name. The credential
            is resolved from the token fixture, so a case cannot name a scope
            that no principal actually has.
        expect_tools: Tools a correct answer uses. Order-insensitive.
        expect_arguments: Per tool, the arguments a correct call carries. Only
            the named arguments are checked; an unnamed one is free.
        expect_documents: Filenames whose chunks a correct answer cites.
            Resolved to document ids by RAG's own rule.
        expect_both_sources: Whether the answer must cite a record *and* a
            document. Plan.md section 12's hybrid metric.
        expect_refusal: Whether at least one tool call must be refused before
            dispatch. Security cases only.
        expect_no_dispatch: Whether *nothing* may reach a handler.
        expect_empty: Whether the tools must return nothing -- the shape a
            cross-scope read takes, which is an empty result and not an error.
        expect_rows: Whether the tools must return *something*. The positive
            control for the line above: without it, a system that returned
            nothing to anybody would pass every cross-scope case in this file.
        forbid: Substrings that must not appear in the answer, in any tool
            payload the model saw, or in the trace. Restricted-field leakage.
        control_sql: A human-authored query that independently computes what
            the tool should have returned.
        expect_row: The row ``control_sql`` must produce. Stated as well as
            computed, so a control query that silently changed meaning fails
            rather than agreeing with a tool that changed the same way.
        note: Why this case exists, where that is not obvious.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    question_class: QuestionClass
    principal: str = Field(min_length=1)

    expect_tools: tuple[str, ...] = ()
    expect_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expect_documents: tuple[str, ...] = ()
    expect_both_sources: bool = False
    expect_refusal: bool = False
    expect_no_dispatch: bool = False
    expect_empty: bool = False
    expect_rows: bool = False
    forbid: tuple[str, ...] = ()
    control_sql: str | None = None
    expect_row: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None

    replay: ReplayScript

    @model_validator(mode="after")
    def _expectations_are_coherent(self) -> GoldenCase:
        """Reject a case that cannot be satisfied, or that checks nothing.

        Raises:
            ValueError: If the case contradicts itself. A case that expects a
                tool it never scripts measures nothing in replay mode; a case
                that expects both a refusal and a dispatch of the same call
                cannot be satisfied by any implementation.
        """
        scripted = {call.tool for call in self.replay.calls}

        if self.expect_no_dispatch and not self.expect_refusal:
            raise ValueError("expect_no_dispatch implies expect_refusal; state both")

        if self.expect_arguments.keys() - scripted:
            raise ValueError(
                "expect_arguments names tools the replay script never calls: "
                f"{sorted(self.expect_arguments.keys() - scripted)}"
            )

        if self.control_sql is not None and not self.expect_row:
            raise ValueError("control_sql without expect_row checks nothing")

        if self.expect_both_sources and self.question_class is not QuestionClass.HYBRID:
            raise ValueError("expect_both_sources belongs to a hybrid case")

        if self.expect_empty and self.expect_rows:
            raise ValueError("expect_empty and expect_rows contradict each other")

        if self.question_class is QuestionClass.SECURITY and not (
            self.expect_refusal or self.forbid or self.expect_empty or self.expect_rows
        ):
            raise ValueError(
                "a security case must assert something: a refusal, an empty "
                "result, a returned result, or a forbidden value"
            )

        return self


class GoldenSet(BaseModel):
    """Every golden case loaded from disk."""

    model_config = ConfigDict(frozen=True)

    cases: tuple[GoldenCase, ...]

    def __len__(self) -> int:
        """How many cases there are."""
        return len(self.cases)

    def of_class(self, question_class: QuestionClass) -> tuple[GoldenCase, ...]:
        """Every case in one metric group."""
        return tuple(case for case in self.cases if case.question_class is question_class)

    @property
    def counts(self) -> dict[str, int]:
        """How many cases per class, for the report header."""
        return {
            question_class.value: len(self.of_class(question_class))
            for question_class in QuestionClass
        }


def default_golden_directory() -> Path:
    """Where the golden files live."""
    return Path(__file__).parent / "golden"


def load_golden_set(directory: Path | None = None) -> GoldenSet:
    """Load and validate every golden file in a directory.

    Args:
        directory: Where to read from. Defaults to the packaged golden set.

    Returns:
        The cases, in a stable order: by file name, then by the order written.
        Stable because a report whose rows reorder between runs cannot be
        diffed, and a regression gate that cannot diff is a gate that gets
        ignored.

    Raises:
        GoldenSetError: If a file is unreadable, malformed, declares an
            unrecognised schema version, or repeats a case id. A repeated id
            makes two different results share one row in the report.
    """
    root = directory or default_golden_directory()
    if not root.is_dir():
        raise GoldenSetError(f"{root} is not a directory")

    cases: list[GoldenCase] = []
    seen: set[str] = set()

    for path in sorted(root.glob("*.yaml")):
        for index, entry in enumerate(_read_cases(path)):
            case = _parse(entry, path=path, index=index)
            if case.id in seen:
                raise GoldenSetError(f"{path} repeats case id {case.id!r}")
            seen.add(case.id)
            cases.append(case)

    if not cases:
        raise GoldenSetError(f"{root} contains no golden cases")

    return GoldenSet(cases=tuple(cases))


def _read_cases(path: Path) -> list[Any]:
    """Read one golden file and return its raw case entries."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GoldenSetError(f"{path} could not be read: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise GoldenSetError(f"{path} is not valid YAML") from exc

    if not isinstance(loaded, dict):
        raise GoldenSetError(f"{path} must be a mapping at the top level")

    version = loaded.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise GoldenSetError(
            f"{path} declares schema_version {version!r}; this build understands "
            f"only {SUPPORTED_SCHEMA_VERSION}"
        )

    entries = loaded.get("cases")
    if not isinstance(entries, list):
        raise GoldenSetError(f"{path} must contain a 'cases' list")
    return entries


def _parse(entry: object, *, path: Path, index: int) -> GoldenCase:
    """Validate one entry into a case."""
    if not isinstance(entry, dict):
        raise GoldenSetError(f"{path} case {index} is not a mapping")

    try:
        return GoldenCase.model_validate(entry)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'case'}: {error['msg']}"
            for error in exc.errors()
        )
        raise GoldenSetError(f"{path} case {index} is invalid: {problems}") from exc
