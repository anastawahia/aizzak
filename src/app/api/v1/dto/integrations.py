"""Integrations DTOs — 03-api-spec §2. Phase 6.1-و-4-1 wrote the four
connection-side shapes (``ConnectorOut`` · ``ConnectionCreateIn`` ·
``ConnectionOut`` · ``AuthorizeOut``); و-4-3 adds the three MCP/tool ones
(``McpServerCreateIn`` · ``McpServerOut`` · ``ToolOut``).

**No token, no ``token_ref``, no ``auth_ref``, no ciphertext, in any shape
here.** The
aggregate holds a ``CipherRef`` and an expiry; only the expiry crosses the
wire. That is FR-121/INV-I1 restated at the boundary, and it is enforced by
these classes being explicit field lists rather than a dump of the entity:
adding a token to a response would take an edit to this file, which is
exactly the kind of edit a reviewer notices.

``ConnectionOut.expires_at`` is the one thing a client learns about the
stored token — *when* it stops working, never *what* it is. It is null for
any row that never completed a handshake, which is also how a client tells a
``pending`` row apart from one that is merely idle.

``last_error`` is deliberately absent even though the aggregate carries it
(the ``DocumentOut``-has-no-``error`` precedent, §3.64): a provider's failure
text is an internal diagnostic, and ``status='error'`` is the fact that
changes a client's behaviour.

``ConnectionCreateIn`` carries **no ``redirect_uri``**. Where a third party
sends the user back to is the platform's decision, derived from
``IntegrationsSettings.oauth_redirect_base_url`` by the Composition Root —
a client-supplied redirect is the classic open-redirect hole, and
``BeginConnection``'s guard exists to police exactly that value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConnectorOut(BaseModel):
    """One entry of the deployment's connector catalog."""

    key: str
    name: str
    scopes: list[str]
    auth_type: str


class ConnectionCreateIn(BaseModel):
    """Begin connecting a connector. ``scopes`` defaults to empty, which
    means "the connector's own defaults" — the provider decides what an
    unqualified consent screen asks for."""

    connector_key: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=list)


class ConnectionOut(BaseModel):
    """A connection's lifecycle state. No token material of any kind."""

    id: str
    connector_key: str
    display_name: str | None
    status: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime


class AuthorizeOut(BaseModel):
    """Where to send the user, and the one-time nonce bound to that URL."""

    authorize_url: str
    state: str


class McpServerCreateIn(BaseModel):
    """Register a remote MCP server (6.1-و-4-3).

    ``auth`` is write-only by shape: it goes in as a free-form object (a
    bearer header, an API key — whatever the endpoint wants), is encrypted
    through Transit before the aggregate ever sees it, and there is no
    response field it can come back out through. That is INV-I1 for MCP
    auth, matching what ``ConnectionOut`` does for OAuth tokens.

    ``transport`` defaults to ``http`` and is checked twice — against the
    deployment's configured allow-list and against ``McpEndpoint``'s v1
    remote-only rule (INV-I2) — so a ``stdio`` server is refused with
    ``integrations.mcp_transport_unsupported``/422 rather than half-created.
    """

    name: str = Field(min_length=1)
    endpoint_url: str = Field(min_length=1)
    transport: str = "http"
    auth: dict[str, Any] | None = None


class McpServerOut(BaseModel):
    """A registered MCP server. **No ``auth_ref``, no ciphertext** — same
    explicit-field-list discipline as ``ConnectionOut``.

    ``status`` is the whole answer to "is this thing in use?": ``disabled``
    is what ``DELETE`` produces and the row stays listed, holding its
    workspace-unique name until a re-registration revives it.
    """

    id: str
    name: str
    endpoint_url: str
    transport: str
    status: str
    created_at: datetime


class ToolOut(BaseModel):
    """One tool the workspace can invoke *through an agent* (6.1-و-4-3).

    ``name`` is the ``<prefix>.<tool>`` handle and ``source`` its provenance
    (``connector:<id>`` / ``mcp:<name>``) — both are the caller's own
    workspace's, and neither is a capability: this API can name a tool but
    offers no way to run one (``IntegrationsUseCases``' ``tools`` field is
    typed ``ToolDiscovery``, which has no ``invoke_tool``).

    ``parameters`` is the tool's JSON-Schema-shaped argument spec, passed
    through as the connector or MCP server described it. Untouched on
    purpose: rewriting a third party's schema in the API layer is how a
    client's generated call ends up not matching what the tool accepts.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    source: str
