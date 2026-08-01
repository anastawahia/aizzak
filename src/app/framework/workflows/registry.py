"""``WorkflowRegistry`` — the static, code-registered workflow catalog (02 §3.4;
the Registries family of ARC-06, D-09).

A kernel-pure in-memory map keyed by ``definition.key``. It holds the STATIC
workflow definitions wired at boot from ``framework/workflows/definitions/``
(D-09 — definitions are code, not data), and is the one validating boundary a
definition passes through, exactly as ``InMemoryAgentRegistry.register`` is for
agent metadata (4.1) and ``InMemoryToolRegistry.register`` is for tools (4.3).

**Strict core (the registry precedent, 4.1/4.3).** ``register`` REFUSES a
duplicate ``key`` (two definitions claiming one key is an authoring error, not
last-one-wins), and REFUSES the R6 family this repo shipped in 2.5/2.6/2.7 — a
non-``WorkflowDefinition``, a non-string/blank ``key``, a ``steps`` that is not
a tuple, a non-``WorkflowStep`` element, a blank ``agent_key``, an ``input_map``
that is not a ``dict[str, str]`` — so hostile/malformed input cannot register
and detonate far
away (a raw ``AttributeError``/``TypeError`` from the engine's run path). It
also enforces the two NFR bounds at boot, not at request N (the 2.9 lesson —
a typed table fails fast at startup): a workflow has **1-10 steps**
(07-nfr-slo "أقصى وكلاء في Workflow واحد = 10 خطوات"; an empty pipeline is
meaningless). Linearity ("خطّي v1 بلا حلقات") needs no check — ``steps`` is a
flat tuple with no branch structure to express.

``get`` on an unknown key is a ``NotFoundError`` (404 — the workflow a caller
named does not exist); a non-string key is a caller bug (422). ``list()``
(deterministic, key-sorted) is a small extension over 02 §3.4's ``register``/
``get``, mirroring the sibling ``AgentRegistry.list`` that IS in the same
contract section — a ``GET /workflows`` and tests need to enumerate
definitions deterministically (doc-sync recommendation to 02 §3.4).

No logging here: deterministic map operations, nothing to observe — the
``AgentRegistry``/``ProviderResolver`` precedent.
"""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Protocol

from app.framework.errors import NotFoundError, ValidationError
from app.framework.workflows.engine import WorkflowDefinition, WorkflowStep

# 07-nfr-slo: "أقصى وكلاء في Workflow واحد = 10 خطوات" (OQ-02, client-signed).
_MAX_STEPS = 10


class WorkflowRegistry(Protocol):
    """02 §3.4 verbatim, plus ``list()`` (see module docstring)."""

    def register(self, definition: WorkflowDefinition) -> None: ...

    def get(self, key: str) -> WorkflowDefinition: ...

    def list(self) -> builtins.list[WorkflowDefinition]: ...


class InMemoryWorkflowRegistry:
    """The concrete registry — a strict, deterministic, in-memory map."""

    def __init__(self) -> None:
        self._defs: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        if not isinstance(definition, WorkflowDefinition):
            raise ValidationError(
                f"definition must be a WorkflowDefinition (got {type(definition).__name__})"
            )
        # The key is this map's whole coordinate system, and WorkflowDefinition
        # is a deliberately dumb carrier — a non-str key would register fine
        # and detonate far away in list()'s sort (the R6 action-at-a-distance
        # family, refused at the boundary instead).
        if not isinstance(definition.key, str) or not definition.key.strip():
            raise ValidationError(
                f"definition.key must be a non-empty string (got {type(definition.key).__name__})"
            )
        self._validate_steps(definition)
        if definition.key in self._defs:
            raise ValidationError(f"workflow key {definition.key!r} is already registered")
        self._defs[definition.key] = definition

    def get(self, key: str) -> WorkflowDefinition:
        # The `or` keeps the raise type-reachable (blank str) under
        # warn_unreachable while catching runtime non-str garbage: an
        # unhashable key would otherwise surface as a raw TypeError from the
        # dict lookup (a caller bug, 422) — distinct from an honest unknown
        # key (404).
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(
                f"workflow key must be a non-empty string (got {type(key).__name__})"
            )
        definition = self._defs.get(key)
        if definition is None:
            # `workflow.unknown` verbatim from 03-api-spec's error catalog
            # ("`workflow.unknown` | 404 | `workflow_key` غير مُعرّف"), not the
            # inherited generic `common.not_found` (4.7-e-1). The catalog gives
            # this its own code so a client can distinguish "no such workflow"
            # from "no such run" on the very same router; the status was always
            # right, only the code was too coarse to act on.
            raise NotFoundError(f"unknown workflow {key!r}", code="workflow.unknown")
        return definition

    def list(self) -> builtins.list[WorkflowDefinition]:
        # Sorted by key: deterministic for GET /workflows and for tests,
        # independent of registration order.
        return [definition for _key, definition in sorted(self._defs.items())]

    @staticmethod
    def _validate_steps(definition: WorkflowDefinition) -> None:
        """Enforce the step invariants at the boundary (D-09, NFR bounds).

        A tuple of 1-10 ``WorkflowStep``s, each with a non-empty ``agent_key``
        and a mapping ``input_map`` — so a malformed definition fails at boot,
        not mid-run.
        """
        key = definition.key
        steps: object = definition.steps
        if not isinstance(steps, tuple):
            raise ValidationError(
                f"workflow {key!r} steps must be a tuple (got {type(steps).__name__})"
            )
        if not steps:
            raise ValidationError(f"workflow {key!r} must have at least one step")
        if len(steps) > _MAX_STEPS:
            raise ValidationError(
                f"workflow {key!r} has {len(steps)} steps, exceeding the max of {_MAX_STEPS}"
            )
        for index, step in enumerate(steps):
            if not isinstance(step, WorkflowStep):
                raise ValidationError(
                    f"workflow {key!r} step {index} must be a WorkflowStep "
                    f"(got {type(step).__name__})"
                )
            if not isinstance(step.agent_key, str) or not step.agent_key.strip():
                raise ValidationError(
                    f"workflow {key!r} step {index} agent_key must be a non-empty string "
                    f"(got {type(step.agent_key).__name__})"
                )
            # v1 projection semantics (engine.py ``_project``): input_map is
            # ``{target_field: context_source_key}``, both strings. Enforce the
            # SHAPE at the boot boundary so a mis-typed map fails at register,
            # not mid-run (the 2.9 lesson). Referential validity (a source key
            # actually present at run time) stays the engine's runtime check.
            input_map: object = step.input_map
            if not isinstance(input_map, dict):
                raise ValidationError(
                    f"workflow {key!r} step {index} input_map must be a mapping "
                    f"(got {type(input_map).__name__})"
                )
            for field, source in input_map.items():
                if not isinstance(field, str) or not isinstance(source, str):
                    raise ValidationError(
                        f"workflow {key!r} step {index} input_map must map field "
                        f"names to context keys (str -> str)"
                    )


if TYPE_CHECKING:

    def _conforms(registry: InMemoryWorkflowRegistry) -> WorkflowRegistry:
        """mypy-gate structural proof that the concrete class satisfies the
        §3.4 port (the ``InMemoryAgentRegistry`` / ``InMemoryToolRegistry``
        precedent)."""
        return registry
