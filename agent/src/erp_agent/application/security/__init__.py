"""Turning a credential into a scope, before anything is read."""

from __future__ import annotations

from erp_agent.application.security.authentication import Authenticate, AuthenticatedCaller

__all__ = ["Authenticate", "AuthenticatedCaller"]
