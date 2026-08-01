"""``AgentMetadata`` — the plugin manifest contract (02-port-contracts §3.2, D-13).

Every agent folder ships a ``manifest.py`` whose module-level ``METADATA`` is
one of these (11-agent-authoring-guide §2). ``key`` is the agent's identity
everywhere: the ``/agents/{key}/invoke`` path, the ``(workspace_id, agent_key)``
conversation key (D-06), and the registry lookup.

The dataclass itself is deliberately contract-literal and dumb — validation
(key shape, key == folder name, manifest↔agent binding) is the
``PluginLoader``'s job at the discovery boundary, the one place untrusted
plugin input enters (the ``ResolvedProvider`` precedent, 2.9: parse at the
boundary, keep the carrier frozen and unopinionated).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentMetadata:
    """Identity, capabilities and permissions of one agent plugin."""

    key: str  # unique, snake_case, == the agent's folder name
    name: str
    version: str
    description: str
    capabilities: frozenset[str]
    required_permissions: frozenset[str]  # checked by RBAC before any run
    default_tools: tuple[str, ...] = ()
