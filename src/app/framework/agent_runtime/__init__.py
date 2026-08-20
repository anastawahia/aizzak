"""Agent runtime — the plugin contract types (02-port-contracts §3.2), the
``AgentRegistry`` (02 §3.4), the ``PluginLoader`` (FR-31, D-13) and the FR-15
``AgentLifecycleExecutor`` (4.2) that drives them."""

from __future__ import annotations

from app.framework.agent_runtime.base_agent import (
    AgentDependencies,
    AgentEvent,
    AgentRequest,
    BaseAgent,
)
from app.framework.agent_runtime.deps_ports import (
    FileReadView,
    FilesAccess,
    KnowledgeAccess,
    MediaRequesting,
    RequestedMediaView,
    ResolvedLLM,
    RetrievedChunkView,
)
from app.framework.agent_runtime.executor import AgentLifecycleExecutor
from app.framework.agent_runtime.file_reading import read_text_file
from app.framework.agent_runtime.lifecycle import AgentLifecycle
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.plugin_loader import (
    PluginFailure,
    PluginLoader,
    PluginLoadReport,
)
from app.framework.agent_runtime.registry import AgentRegistry, InMemoryAgentRegistry
from app.framework.agent_runtime.source_label import format_labeled_chunk

__all__ = [
    "AgentDependencies",
    "AgentEvent",
    "AgentLifecycle",
    "AgentLifecycleExecutor",
    "AgentMetadata",
    "AgentRegistry",
    "AgentRequest",
    "BaseAgent",
    "FileReadView",
    "FilesAccess",
    "InMemoryAgentRegistry",
    "KnowledgeAccess",
    "MediaRequesting",
    "PluginFailure",
    "PluginLoadReport",
    "PluginLoader",
    "RequestedMediaView",
    "ResolvedLLM",
    "RetrievedChunkView",
    "format_labeled_chunk",
    "read_text_file",
]
