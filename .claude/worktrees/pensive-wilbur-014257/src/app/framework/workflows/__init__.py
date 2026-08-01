"""Workflows — the multi-agent pipeline feature (02 §3.3/§3.4, D-04/09/12): the
frozen carriers + ``WorkflowEngine`` port + concrete ``SequentialWorkflowEngine``
(engine.py) and the ``WorkflowRegistry`` (registry.py)."""

from __future__ import annotations

from app.framework.workflows.engine import (
    AgentDepsProvider,
    SequentialWorkflowEngine,
    StaticAgentDeps,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowResult,
    WorkflowStep,
)
from app.framework.workflows.registry import InMemoryWorkflowRegistry, WorkflowRegistry

__all__ = [
    "AgentDepsProvider",
    "InMemoryWorkflowRegistry",
    "SequentialWorkflowEngine",
    "StaticAgentDeps",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowRegistry",
    "WorkflowResult",
    "WorkflowStep",
]
