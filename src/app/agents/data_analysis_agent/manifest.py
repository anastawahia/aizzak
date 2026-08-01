"""``data_analysis_agent`` manifest (11 §2, FR-20.2)."""

from __future__ import annotations

from app.framework.agent_runtime.metadata import AgentMetadata

METADATA = AgentMetadata(
    key="data_analysis_agent",
    name="Data Analysis Agent",
    version="1.0.0",
    description="Analyzes a workspace data file and answers questions about it via the LLM.",
    capabilities=frozenset({"chat", "data_analysis", "streaming"}),
    required_permissions=frozenset({"agents:invoke", "files:read"}),
)
