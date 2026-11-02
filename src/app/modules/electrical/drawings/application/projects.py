"""The WireTwin project service (SRS D-29: one project per space; FR-ING-01, FR-ADM-04).

A project is created with its first profile version in ONE unit of work: ``default`` when the
caller sent no profile, ``user`` when it did. The project's profile is append-only: replacing it
adds a version, never edits one. A document's own override and the profile STG-01 proposes live
in the document's series (``DocumentService``).

Profile bodies are validated by ``DrawingProfile.from_body`` against the NAMES in the packaged
rules (``wire_number_patterns``); a violation is ``422 electrical.invalid_profile`` naming the
field. Reads never hide a project from its own workspace; another tenant's is a 404.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.types import Uuid
from app.modules.electrical.drawings.application._common import (
    Clock,
    invalid_input,
    invalid_profile,
    new_profile_version,
    retried,
)
from app.modules.electrical.drawings.domain.errors import InvalidDrawingInput, InvalidProfile
from app.modules.electrical.drawings.domain.profile import (
    DrawingProfile,
    ProfileSource,
    ProfileVersion,
)
from app.modules.electrical.drawings.domain.project import (
    Project,
    normalize_description,
    normalize_project_name,
)
from app.modules.electrical.drawings.ports.repository import ProfileRepository, ProjectRepository
from app.modules.electrical.drawings.ports.rules import RuleSource
from app.modules.electrical.drawings.ports.spaces import ActiveSpaces
from app.modules.electrical.shared.normalization import canonical_form
from app.modules.electrical.shared.rules import RuleError

_VOLTAGE_LABEL_TEXT = (
    "each label must be 1..16 characters of A-Z 0-9 + - with a letter or a digit, "
    "and must not be readable as another kind of identifier (a rail such as -48V or +24V is)"
)


def parse_profile(rules: RuleSource, body: Mapping[str, object]) -> DrawingProfile:
    """``DrawingProfile.from_body`` over the packaged rule names, errors as 422.

    The profile is then run through the same narrowing a document is parsed with
    (``IdentifierRules.narrowed``), so a voltage label that would hide ``-K5`` / ``KM1`` /
    ``X1:14``, one made of signs or holding a hidden character, and an aspect the rules do not
    have are refused HERE, at the trust boundary (INC1-Q-5, R2-2). The messages are fixed:
    ``narrowed`` echoes the client's text, this does not. Labels are stored canonical."""
    packaged = rules.identifier_rules()
    try:
        profile = DrawingProfile.from_body(
            body, known_patterns=frozenset(packaged.wire_number_patterns)
        )
    except InvalidProfile as exc:
        raise invalid_profile(exc) from exc
    selection = profile.rule_selection()
    try:
        packaged.narrowed(replace(selection, extra_voltage_labels=()))
    except RuleError:
        raise invalid_profile(
            InvalidProfile(
                "extra_aspects", f"unknown aspect; known: {', '.join(packaged.extra_aspects)}"
            )
        ) from None
    try:
        # under EVERY convention (a label must not hide `KM1` in a Chinese drawing either), and
        # under the profile's own, the way a document is parsed with it
        packaged.narrowed(replace(selection, conventions=frozenset()))
        packaged.narrowed(selection)
    except RuleError:
        raise invalid_profile(InvalidProfile("voltage_labels", _VOLTAGE_LABEL_TEXT)) from None
    return replace(
        profile,
        voltage_labels=tuple(sorted({canonical_form(label) for label in profile.voltage_labels})),
    )


@dataclass(frozen=True, slots=True)
class ProjectDetail:
    """A project with its current profile version."""

    project: Project
    profile: ProfileVersion


class ProjectService:
    def __init__(
        self,
        projects: ProjectRepository,
        profiles: ProfileRepository,
        spaces: ActiveSpaces,
        rules: RuleSource,
        uow: UnitOfWork,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._projects = projects
        self._profiles = profiles
        self._spaces = spaces
        self._rules = rules
        self._uow = uow
        self._clock = clock

    async def create(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid,
        name: str,
        description: str | None,
        profile: Mapping[str, object] | None,
    ) -> ProjectDetail:
        """One project per space (``409 electrical.project_exists`` from the index); an unknown,
        foreign or deleted space is a 404."""
        try:
            clean_name = normalize_project_name(name)
            clean_description = normalize_description(description)
        except InvalidDrawingInput as exc:
            raise invalid_input(exc) from exc
        parsed = None if profile is None else parse_profile(self._rules, profile)
        if await self._spaces.get_active(ctx, space_id) is None:
            raise NotFoundError("space not found")
        now = self._clock()
        project = Project(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            space_id=space_id,
            name=clean_name,
            description=clean_description,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        first = new_profile_version(
            ctx,
            project_id=project.id,
            document_id=None,
            source=ProfileSource.DEFAULT if parsed is None else ProfileSource.USER,
            profile=DrawingProfile.default() if parsed is None else parsed,
            now=now,
        )
        async with self._uow.begin(ctx):
            await self._projects.add(ctx, project)
            stored = await self._profiles.append(ctx, first)
        return ProjectDetail(project=project, profile=stored)

    async def get(self, ctx: ExecutionContext, project_id: Uuid) -> ProjectDetail:
        project = await self._require(ctx, project_id)
        return ProjectDetail(project=project, profile=await self._current(ctx, project))

    async def list(
        self, ctx: ExecutionContext, *, space_id: Uuid, limit: int, cursor: str | None
    ) -> Page[Project]:
        return await self._projects.list_by_space(ctx, space_id, limit=limit, cursor=cursor)

    async def update(
        self, ctx: ExecutionContext, project_id: Uuid, *, name: str | None, description: str | None
    ) -> ProjectDetail:
        """``None`` leaves a field alone; a blank description clears it."""

        async def work() -> Project:
            project = await self._require(ctx, project_id)
            before = (project.name, project.description)
            now = self._clock()
            try:
                if name is not None:
                    project.rename(name, now)
                if description is not None:
                    project.describe(description, now)
            except InvalidDrawingInput as exc:
                raise invalid_input(exc) from exc
            if (project.name, project.description) == before:
                return project  # nothing changed: nothing written (the domain's own no-op rule)
            async with self._uow.begin(ctx):
                await self._projects.save(ctx, project)
            return project

        project = await retried(work)
        return ProjectDetail(project=project, profile=await self._current(ctx, project))

    async def get_profile(self, ctx: ExecutionContext, project_id: Uuid) -> ProfileVersion:
        return await self._current(ctx, await self._require(ctx, project_id))

    async def replace_profile(
        self, ctx: ExecutionContext, project_id: Uuid, body: Mapping[str, object]
    ) -> ProfileVersion:
        """Append a ``user`` version. Documents already processed are not reprocessed: that
        is an explicit call (HLD §7.5)."""
        parsed = parse_profile(self._rules, body)
        await self._require(ctx, project_id)

        async def work() -> ProfileVersion:
            version = new_profile_version(
                ctx,
                project_id=project_id,
                document_id=None,
                source=ProfileSource.USER,
                profile=parsed,
                now=self._clock(),
            )
            async with self._uow.begin(ctx):
                return await self._profiles.append(ctx, version)

        return await retried(work)

    async def _require(self, ctx: ExecutionContext, project_id: Uuid) -> Project:
        project = await self._projects.get(ctx, project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project

    async def _current(self, ctx: ExecutionContext, project: Project) -> ProfileVersion:
        version = await self._profiles.latest(ctx, project_id=project.id, document_id=None)
        if version is None:  # a project is created with its first version: this is a defect
            raise NotFoundError("project profile not found")
        return version


__all__ = ["ProjectDetail", "ProjectService", "parse_profile"]
