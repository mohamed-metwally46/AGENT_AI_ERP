"""Use cases: one class per thing a caller can ask the system to do."""

from __future__ import annotations

from erp_agent.application.use_cases.answer_erp_question import (
    AnsweredQuestion,
    AnswerErpQuestion,
)

__all__ = ["AnswerErpQuestion", "AnsweredQuestion"]
