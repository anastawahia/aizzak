"""The rules error, alone in a leaf module so ``systems`` and the package can both import it."""

from __future__ import annotations


class RuleError(ValueError):
    """The rules mapping has the wrong shape, or a rule does not compile."""
