"""Ports: the interfaces the domain requires of the outside world."""

from __future__ import annotations

from erp_agent.domain.ports.data_source import ReadOnlyDataSource

__all__ = ["ReadOnlyDataSource"]
