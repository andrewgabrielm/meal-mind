"""Price resolution: live QuickCommerce -> price_cache -> seeded prices.

Prices are fetched at pipeline step 5, AFTER recipes are chosen — you cannot
price ingredients until you know which recipes were picked, and fetching
earlier wastes QuickCommerce credits on commodities you may not use.

This module is pure logic: the router supplies cached/seeded rows via the
repository and (optionally) a live client; newly fetched live packs are
returned for the caller to persist. No SQL here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from scripts.ingredient_tables import unit_weight

from .optimizer import Pack
from .quickcommerce import LivePack, QuickCommerceClient


@dataclass
class ResolvedPrice:
    commodity: str
    packs: list[Pack]
    source: str                      # live | cache | seed


@dataclass
class PriceResolution:
    by_commodity: dict[str, ResolvedPrice]
    to_cache: list[LivePack] = field(default_factory=list)

    def packs_dict(self) -> dict[str, list[Pack]]:
        return {c: r.packs for c, r in self.by_commodity.items() if r.packs}

    def source_of(self, commodity: str) -> str:
        r = self.by_commodity.get(commodity)
        return r.source if r else "seed"


def _search_words(commodity: str) -> str:
    return commodity.replace("_", " ")


def resolve_prices(
    commodities: list[str],
    cached_packs: dict[str, list[Pack]],     # rows with source='live' (price_cache)
    seeded_packs: dict[str, list[Pack]],     # rows with source='seed'
    live_client: QuickCommerceClient | None = None,
) -> PriceResolution:
    out: dict[str, ResolvedPrice] = {}
    to_cache: list[LivePack] = []

    for c in commodities:
        # 1. live
        if live_client is not None:
            live = live_client.fetch_packs(c, _search_words(c), unit_weight(c))
            if live:
                out[c] = ResolvedPrice(
                    commodity=c,
                    packs=[Pack(size_g=p.pack_size_g, price_rs=p.price_rs) for p in live],
                    source="live",
                )
                to_cache.extend(live)
                continue
        # 2. cache
        if cached_packs.get(c):
            out[c] = ResolvedPrice(commodity=c, packs=cached_packs[c], source="cache")
            continue
        # 3. seed — the backup and the testing path; must work with no key
        out[c] = ResolvedPrice(commodity=c, packs=seeded_packs.get(c, []), source="seed")

    return PriceResolution(by_commodity=out, to_cache=to_cache)
