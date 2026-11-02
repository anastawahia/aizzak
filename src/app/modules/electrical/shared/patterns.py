"""Compiled rule patterns, cached. Leaf module: imports nothing from the kernel."""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=128)
def compiled(pattern: str) -> re.Pattern[str]:
    """The compiled form of a rule's regular expression, cached (a match never
    recompiles) and always ASCII-only (``\\d`` never matches a non-Latin digit).
    Raises ``re.error`` when the pattern is invalid."""
    return re.compile(pattern, re.ASCII)
