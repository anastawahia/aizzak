"""The same-name seam (PLT-24): bound to ``files``' ``FindNamesakes`` in the Composition Root.

Drawings asks which older live files a new registration would replace (and which it would
leave), so STG-00 can refuse a registration whose namesake is still a live document, and the
preview can say what a replacement would take with it. The rule itself ("same name up to case
and normalisation, same tags, same space, strictly older") lives behind this port, once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class NamesakeView(Protocol):
    @property
    def replaced(self) -> Sequence[str]: ...

    @property
    def kept(self) -> Sequence[str]: ...


class OlderNamesakes(Protocol):
    async def execute(self, ctx: ExecutionContext, file_id: Uuid) -> Sequence[Uuid]: ...

    async def preview(self, ctx: ExecutionContext, file_id: Uuid) -> NamesakeView: ...
