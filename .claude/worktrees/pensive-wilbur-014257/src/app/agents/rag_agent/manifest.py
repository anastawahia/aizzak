"""``rag_agent`` manifest — identity, capabilities, permissions (11 §2, FR-20).

``PluginLoader`` requires module-level ``METADATA`` that IS an
``AgentMetadata`` and whose ``key`` is snake_case AND equals the folder name
(``rag_agent``). ``agent.py`` binds it with ``metadata = METADATA``.
"""

from __future__ import annotations

from app.framework.agent_runtime.metadata import AgentMetadata

METADATA = AgentMetadata(
    key="rag_agent",
    name="RAG Agent",
    version="1.0.0",
    description="Answers from workspace knowledge via hybrid retrieval + LLM streaming.",
    capabilities=frozenset({"chat", "retrieval", "streaming"}),
    required_permissions=frozenset({"agents:invoke", "knowledge:read"}),
)
