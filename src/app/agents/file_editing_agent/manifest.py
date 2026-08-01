"""``file_editing_agent`` manifest (11 §2, FR-20.5)."""

from __future__ import annotations

from app.framework.agent_runtime.metadata import AgentMetadata

METADATA = AgentMetadata(
    key="file_editing_agent",
    name="File Editing Agent",
    version="1.0.0",
    description="Applies an instructed edit to a workspace text file and returns the result.",
    capabilities=frozenset({"chat", "file_editing", "streaming"}),
    required_permissions=frozenset({"agents:invoke", "files:read"}),
)
