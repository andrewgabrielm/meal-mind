"""Drawing stock down when a meal is actually cooked.

Pure allocation logic — no SQL, no HTTP. Given the pantry as plain dicts and
the grams a recipe needs, decide which physical items to draw from and by how
much.

Oldest stock is consumed first. That is not an accounting convention here: it
is the same principle feature (b) exists for — the item closest to spoiling is
the one that should be eaten. Using newest-first would let old stock rot while
fresh stock is cooked.

An item drawn down to zero yields a CENSORED observation for the decay model
(consumed while still good), which is exactly the right-censored data point the
Weibull posterior wants. Cooking therefore teaches the spoilage model, without
the user reporting anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Draw:
    """One physical pantry item, partially or fully consumed."""
    pantry_item_id: int
    commodity: str
    item_class: str
    storage: str
    grams_taken: float
    grams_left: float
    age_days: float

    @property
    def exhausted(self) -> bool:
        return self.grams_left <= 1e-6


@dataclass
class Consumption:
    draws: list[Draw] = field(default_factory=list)
    # commodity -> grams the pantry could not supply (cooked from shopping)
    short: dict[str, float] = field(default_factory=dict)

    @property
    def taken(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for d in self.draws:
            out[d.commodity] = out.get(d.commodity, 0.0) + d.grams_taken
        return {c: round(g, 1) for c, g in out.items()}

    @property
    def exhausted_draws(self) -> list[Draw]:
        return [d for d in self.draws if d.exhausted]


def plan_consumption(pantry_items: list[dict], needs_g: dict[str, float]) -> Consumption:
    """pantry_items: [{id, commodity, item_class, storage, quantity_g, age_days}].
    needs_g: commodity -> grams the cooked recipe used."""
    by_commodity: dict[str, list[dict]] = {}
    for it in pantry_items:
        by_commodity.setdefault(it["commodity"], []).append(it)
    # oldest first — the at-risk item gets eaten
    for items in by_commodity.values():
        items.sort(key=lambda i: -i.get("age_days", 0.0))

    result = Consumption()
    for commodity, need in needs_g.items():
        remaining = need
        for item in by_commodity.get(commodity, []):
            if remaining <= 1e-6:
                break
            have = item.get("quantity_g", 0.0)
            if have <= 1e-6:
                continue
            take = min(have, remaining)
            remaining -= take
            result.draws.append(Draw(
                pantry_item_id=item["id"], commodity=commodity,
                item_class=item.get("item_class", ""), storage=item.get("storage", "room"),
                grams_taken=round(take, 1), grams_left=round(have - take, 1),
                age_days=item.get("age_days", 0.0),
            ))
        if remaining > 1e-6:
            # normal: the shopping list supplied it, it was never in the pantry
            result.short[commodity] = round(remaining, 1)
    return result
