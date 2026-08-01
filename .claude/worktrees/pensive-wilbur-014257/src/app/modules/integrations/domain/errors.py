"""Integrations domain errors (pure — 06-domain-models §9).

Module-local rule violations, translated into the shared framework hierarchy
(`framework/errors`) at the application boundary — the domain itself never
imports framework code (§5.1 option A, the media/knowledge precedent).
"""

from __future__ import annotations


class IntegrationError(Exception):
    """Base for every integrations domain-rule violation."""


class InvalidIntegrationInput(IntegrationError):
    """A value object or entity received malformed input (→ 422 at the
    application boundary)."""


class UnsupportedMcpTransport(IntegrationError):
    """MCP transport outside the v1 remote-only allow-list (INV-I2, ARC-15) —
    maps to the stable ``integrations.mcp_transport_unsupported`` code (422,
    03-api-spec §4)."""


class ConnectionStateError(IntegrationError):
    """An illegal ``Connection`` lifecycle transition was attempted (→ 409 at
    the application boundary)."""
