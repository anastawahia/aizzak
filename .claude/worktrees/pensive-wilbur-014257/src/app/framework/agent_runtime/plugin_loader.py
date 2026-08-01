"""``PluginLoader`` — boot-time agent discovery over a folder scan
(FR-31, D-13, 11-agent-authoring-guide §5; no signature in 02 — this file IS
the reference).

**Every import here is dynamic on purpose** — ``importlib.import_module``
over runtime strings enumerated by ``pkgutil.iter_modules``. That is D-13's
mechanism verbatim (architecture §07: "يكتشفه عبر importlib"), and it is also
the ONLY thing that keeps import-linter's ``framework-kernel`` contract green:
this module lives in ``app.framework`` and loads ``app.agents.*``, an import
edge the layering forbids statically. A dynamic string import creates no AST
edge for grimp to see; adding a single literal ``import app.agents...`` here
would turn the architecture gate red. The loader handles agent classes only
through the framework's own ``type[BaseAgent]``.

**Scan contract** (drop a folder = a new agent, FR-30):

* the target package (constructor param, default ``app.agents`` — a
  parameter so the discovery/isolation/AC-04 behavior is provable on
  synthetic trees without touching the real five) is imported; failure to
  import the ROOT package passes through untranslated — no agents at all is
  an operator error to fix at boot, not a plugin to isolate;
* every immediate child PACKAGE not starting with ``_`` is a candidate
  (loose ``.py`` files and ``_private`` folders are ignored — an agent is a
  folder by FR-30); candidates are processed in sorted order so registration
  and duplicate-key outcomes are deterministic, independent of filesystem
  enumeration;
* per candidate, the loader imports ``<pkg>.<name>.manifest``, requires a
  module-level ``METADATA`` that IS an ``AgentMetadata`` (an isinstance
  guard, not a duck-walk — the R6 family: garbage in a manifest must not
  escape a boot path as a raw ``AttributeError``), requires
  ``METADATA.key`` to be snake_case AND equal to the folder name (the folder
  is the route of FR-30; equality catches copy-pasted manifests, and the
  regex matters because ``importlib`` will happily import a ``bad-agent``
  folder no ``import`` statement could name), then imports
  ``<pkg>.<name>.agent`` and requires EXACTLY ONE concrete ``BaseAgent``
  subclass defined within the plugin's own package (``__module__`` prefix
  check — a class merely imported from elsewhere does not count, so a
  forbidden cross-agent import cannot double-register; zero is a broken
  plugin, two is ambiguity, both isolated), whose ``metadata`` attribute IS
  (identity, not equality) the manifest's ``METADATA`` — binding the two
  files (11 §3's ``metadata = METADATA`` line);
* the agent CLASS is the factory (D-13; ``cls(ctx, deps)`` matches
  ``BaseAgent.__init__``), registered via ``AgentRegistry.register`` — whose
  duplicate-key refusal is caught by the same isolation guard, so of two
  folders claiming one key the sorted-first wins and the second is isolated;
* ANY failure in a candidate — import error, missing/mistyped ``METADATA``,
  key mismatch, ambiguity, duplicate — isolates THAT plugin: logged (the one
  place in ``agent_runtime`` that logs; discovery is an operational event,
  unlike the registry/resolver's deterministic lookups) and aggregated into
  the returned report, while the scan continues (11 §5: a broken plugin must
  not take the platform down).

Re-running the scan on a fresh registry at every boot is what implements
deregistration-by-deletion (AC-04): the loader never persists anything, so a
deleted folder simply stops being discovered.

Boot wiring is deferred (the 2.9/2.4 precedent): the consumers of a loaded
registry are ``GET /agents`` (م6) and the orchestrator (4.7), and wiring in
``from_env`` today would only isolate the five still-empty agent stubs. The
Composition Root docstring carries the literal two-line construction.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from dataclasses import dataclass

from app.framework.agent_runtime.base_agent import BaseAgent
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.agent_runtime.registry import AgentRegistry
from app.framework.errors import ValidationError
from app.framework.observability import get_logger

_logger = get_logger(__name__)

_KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")


@dataclass(frozen=True, slots=True)
class PluginFailure:
    """One isolated plugin: folder name + the reason it was rejected."""

    plugin: str
    reason: str


@dataclass(frozen=True, slots=True)
class PluginLoadReport:
    """Outcome of one scan: registered keys + isolated failures, both ordered."""

    loaded: tuple[str, ...]
    failures: tuple[PluginFailure, ...]


class PluginLoader:
    """Scans a package for agent-plugin folders and registers the survivors."""

    def __init__(self, package: str = "app.agents") -> None:
        self._package = package

    def load_into(self, registry: AgentRegistry) -> PluginLoadReport:
        """Discover every agent under the package and register it; isolate
        the broken ones instead of raising (module docstring has the rules)."""
        root = importlib.import_module(self._package)
        if not hasattr(root, "__path__"):
            # A plain module (no __path__) cannot host agent folders; the raw
            # AttributeError this used to be names neither the package nor
            # the mistake — boot errors must be operator-legible.
            raise ValidationError(f"{self._package} is not a package — agent plugins are folders")
        candidates = sorted(
            info.name
            for info in pkgutil.iter_modules(root.__path__)
            if info.ispkg and not info.name.startswith("_")
        )
        loaded: list[str] = []
        failures: list[PluginFailure] = []
        for name in candidates:
            try:
                metadata, agent_cls = self._load_one(name)
                registry.register(metadata, agent_cls)
            except Exception as exc:  # isolation IS the contract (11 §5)
                _logger.warning(
                    "plugin_loader.isolated",
                    extra={"plugin": name, "reason": str(exc)},
                    exc_info=exc,
                )
                failures.append(PluginFailure(plugin=name, reason=str(exc)))
            else:
                _logger.info(
                    "plugin_loader.registered",
                    extra={"plugin": name, "key": metadata.key},
                )
                loaded.append(metadata.key)
        return PluginLoadReport(loaded=tuple(loaded), failures=tuple(failures))

    def _load_one(self, name: str) -> tuple[AgentMetadata, type[BaseAgent]]:
        """Validate one candidate folder end to end, or raise the reason."""
        plugin = f"{self._package}.{name}"
        manifest = importlib.import_module(f"{plugin}.manifest")
        # Typed `object`, not the getattr `Any`: the 2.9 `_parse_routing`
        # pattern — parse the untrusted value from `object` so the isinstance
        # guard actually narrows under mypy --strict.
        candidate: object = getattr(manifest, "METADATA", None)
        if candidate is None:
            raise ValidationError(f"{plugin}.manifest: METADATA is missing")
        if not isinstance(candidate, AgentMetadata):
            raise ValidationError(
                f"{plugin}.manifest: METADATA must be an AgentMetadata "
                f"(got {type(candidate).__name__})"
            )
        metadata = candidate
        # isinstance first: fullmatch on a non-str key raises a bare TypeError
        # — still isolated by the blanket guard, but with an illegible reason.
        if not isinstance(metadata.key, str) or not _KEY_RE.fullmatch(metadata.key):
            raise ValidationError(f"{plugin}.manifest: key {metadata.key!r} is not snake_case")
        if metadata.key != name:
            raise ValidationError(
                f"{plugin}.manifest: key {metadata.key!r} does not match the "
                f"agent folder name {name!r}"
            )
        agent_module = importlib.import_module(f"{plugin}.agent")
        concrete = sorted(
            (
                obj
                for obj in vars(agent_module).values()
                if isinstance(obj, type)
                and issubclass(obj, BaseAgent)
                and obj is not BaseAgent
                and not inspect.isabstract(obj)
                and obj.__module__.startswith(f"{plugin}.")
            ),
            key=lambda cls: cls.__qualname__,
        )
        if not concrete:
            raise ValidationError(f"{plugin}.agent: no concrete BaseAgent subclass")
        if len(concrete) > 1:
            names = ", ".join(cls.__qualname__ for cls in concrete)
            raise ValidationError(
                f"{plugin}.agent: ambiguous — {len(concrete)} concrete BaseAgent "
                f"subclasses ({names}), expected exactly one"
            )
        agent_cls = concrete[0]
        if getattr(agent_cls, "metadata", None) is not metadata:
            raise ValidationError(
                f"{plugin}.agent: {agent_cls.__qualname__}.metadata is not the "
                f"manifest's METADATA object (11 §3 requires `metadata = METADATA`)"
            )
        return metadata, agent_cls
