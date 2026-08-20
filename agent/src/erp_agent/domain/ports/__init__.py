"""Ports: the interfaces the domain requires of the outside world."""

from __future__ import annotations

from erp_agent.domain.ports.access_tokens import AccessTokenPort
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.domain.ports.principal_resolver import PrincipalResolverPort

__all__ = ["AccessTokenPort", "PrincipalResolverPort", "ReadOnlyDataSource"]
