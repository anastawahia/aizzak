"""``LlmPricing`` — what one LLM call costs, in micro-dollars (capacity-plan 6.5).

**Why this exists.** Until 6.5 every charge carried ``cost_micros = 0``, so
the workspace cost budget (``USAGE_DEFAULT_LIMITS``' ``cost_micros``) could
never fire and the ONLY thing standing between a tenant and the platform's
OpenAI bill was the token quota. A cloud provider does not fall over under
load — it sends an invoice — so cost is a capacity dimension in its own right,
and the budget has to be fed a real number to mean anything.

**The unit is the whole trick.** A price is written the way every vendor
publishes it: US dollars per million tokens. One dollar is 1,000,000
micro-dollars, so that same number IS micro-dollars per token — ``0.40`` per
million input tokens is ``0.4`` micros per input token, with no conversion
anywhere. ``cost_micros`` therefore multiplies tokens by the configured
numbers and rounds UP once per charge: a charge is never smaller than what it
stands for, and rounding each call separately would bill a ten-call turn up to
ten micros more than the same tokens in one call.

**Boot refuses a hole, not a typo.** ``for_routes`` refuses to construct when
any ``llm`` route that needs a credential (a cloud provider) has no price —
that is precisely the unmetered spend this step closes, and it must die at
boot rather than be discovered on an invoice. A keyless (local) provider is
free by definition and may NOT be given a price: the entry would read as a
charge the platform never pays, and a budget that ran out on electricity
nobody bills for is a bug report waiting to happen. A price for a model no
route names is allowed — it is how an operator stages a model switch, and the
next route change is checked against it then.

**Decimal, not float.** ``0.4`` is not representable in binary, and a ledger
summed over millions of charges is exactly where that error stops being
theoretical. Prices are parsed through ``str`` into ``Decimal``, so what the
operator wrote is what is multiplied.

**Deliberately absent:** image generation (priced per image, not per token —
``media`` charges nothing yet, `D-04`), embeddings (local), cached-input and
batch discounts (the adapters send neither), and any notion of WHOSE key paid:
a workspace on its own OpenAI key is charged against its budget all the same,
the conservative direction.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.framework.errors import ValidationError
from app.framework.providers.inventory import ConfiguredProvider

_ENTRY_KEYS = frozenset({"input", "output"})
_SEPARATOR = "/"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """One model's published price — USD per million tokens, which is the
    same number as micro-dollars per token (module docstring)."""

    input: Decimal
    output: Decimal


class LlmPricing:
    """The parsed price table and the one arithmetic it exists for."""

    def __init__(
        self, prices: Mapping[tuple[str, str], ModelPrice], keyless: frozenset[str]
    ) -> None:
        self._prices = dict(prices)
        self._keyless = keyless

    @classmethod
    def for_routes(cls, raw: object, providers: Iterable[ConfiguredProvider]) -> LlmPricing:
        """Parse ``LLM_PRICES`` and check it against the routing table, or
        refuse loudly (module docstring, Boot refuses a hole).

        ``providers`` is ``SettingsProviderResolver.configured_providers()``:
        the SAME parsed table every resolution walks, so the check cannot
        disagree with what a request will actually be routed to.
        """
        configured = tuple(providers)
        keyless = frozenset(p.provider for p in configured if p.keyless)
        prices = _parse_prices(raw)
        for provider, model in prices:
            if provider in keyless:
                raise ValidationError(
                    f"llm_prices[{provider}{_SEPARATOR}{model!s}]: {provider!r} is a local "
                    f"provider and costs nothing; remove the entry"
                )
        for entry in configured:
            if entry.keyless:
                continue
            for route in entry.routes:
                if route.namespace != "llm":
                    continue
                if (entry.provider, route.model) not in prices:
                    raise ValidationError(
                        f"llm_prices: the llm route {route.capability!r} sends calls to "
                        f"{entry.provider}{_SEPARATOR}{route.model}, which has no price -- "
                        f"every cloud model needs one, or its spend is never counted "
                        f"(capacity-plan 6.5)"
                    )
        return cls(prices, keyless)

    def cost_micros(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> int:
        """What these tokens cost on ``provider``/``model``, rounded UP to a
        whole micro-dollar. ``0`` for a local provider and for no tokens.

        A cloud model with no price RAISES. ``for_routes`` makes that
        unreachable for every routed model; the raise is for the case it
        cannot see — a model chosen at call time — where answering ``0``
        would be exactly the silent free tier this class exists to remove.
        """
        if provider in self._keyless or (prompt_tokens <= 0 and completion_tokens <= 0):
            return 0
        price = self._prices.get((provider, model))
        if price is None:
            raise ValidationError(
                f"no price is configured for {provider}{_SEPARATOR}{model} (llm_prices)"
            )
        exact = price.input * max(prompt_tokens, 0) + price.output * max(completion_tokens, 0)
        return math.ceil(exact)


def _parse_prices(raw: object) -> dict[tuple[str, str], ModelPrice]:
    """``{"provider/model": {"input": n, "output": n}}`` into typed prices,
    strictly — the ``provider_routing`` rule: a malformed entry dies at boot
    with its own path in the message, never becomes a silent zero."""
    if not isinstance(raw, dict):
        raise ValidationError(
            f'llm_prices: must be an object of "provider/model" -> '
            f"{{input, output}}, got {type(raw).__name__}"
        )
    prices: dict[tuple[str, str], ModelPrice] = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or _SEPARATOR not in key:
            raise ValidationError(f'llm_prices: keys are "provider/model", got {key!r}')
        # The FIRST separator: provider names never contain one, model names
        # may (`meta-llama/llama-3` behind a router), and both must survive.
        provider, _, model = key.partition(_SEPARATOR)
        if not provider.strip() or not model.strip():
            raise ValidationError(f'llm_prices: keys are "provider/model", got {key!r}')
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise ValidationError(
                f"llm_prices[{key}]: must have exactly the keys 'input' and 'output' "
                f"(USD per million tokens)"
            )
        prices[(provider, model)] = ModelPrice(
            input=_price(key, "input", entry["input"]),
            output=_price(key, "output", entry["output"]),
        )
    return prices


def _price(key: str, side: str, value: object) -> Decimal:
    """One non-negative finite number. ``bool`` is refused although it IS an
    ``int`` to Python: ``true`` in a price table is a typo, not a dollar."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValidationError(f"llm_prices[{key}].{side}: must be a number, got {value!r}")
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError(f"llm_prices[{key}].{side}: must be a number, got {value!r}") from exc
    if not price.is_finite() or price < 0:
        raise ValidationError(
            f"llm_prices[{key}].{side}: must be a finite number >= 0, got {value!r}"
        )
    return price
