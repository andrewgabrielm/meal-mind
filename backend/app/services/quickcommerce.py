"""QuickCommerce live price client + quantity parser.

The client is exercised only when QUICKCOMMERCE_API_KEY is set; the seeded
path must keep working with no key and no network. NOTE (known limitation):
commodity matching is token overlap, so "Tomato Ketchup" scores as a tomato
match — a negative-keyword list is needed before this is trusted with real
money. No SQL here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

# ---------------------------------------------------------------- parser

_QTY_RE = re.compile(
    r"(?:(?P<count>\d+)\s*[xX*]\s*)?"
    r"(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|kilogram|g|gm|gms|gram|grams|l|ltr|litre|liter|ml|pc|pcs|piece|pieces|dozen|unit|units)\b",
    re.IGNORECASE,
)

_UNIT_G = {
    "kg": 1000.0, "kilogram": 1000.0,
    "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0,
    # liquids: treated at density ~1.0; milk's 1.03 is inside tolerance here
    "l": 1000.0, "ltr": 1000.0, "litre": 1000.0, "liter": 1000.0, "ml": 1.0,
}
_PIECE_UNITS = {"pc", "pcs", "piece", "pieces", "unit", "units"}


def parse_quantity_g(text: str, piece_weight_g: float = 50.0) -> float | None:
    """'500 g' -> 500, '1 kg' -> 1000, '2 x 250g' -> 500, '1 L' -> 1000,
    '6 pcs' -> 6 * piece_weight, '1 dozen' -> 12 * piece_weight."""
    m = _QTY_RE.search(text or "")
    if not m:
        return None
    count = int(m.group("count")) if m.group("count") else 1
    num = float(m.group("num"))
    unit = m.group("unit").lower()
    if unit == "dozen":
        grams = num * 12 * piece_weight_g
    elif unit in _PIECE_UNITS:
        grams = num * piece_weight_g
    else:
        grams = num * _UNIT_G[unit]
    return count * grams


# ---------------------------------------------------------------- matching

def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 1}


def match_score(commodity_words: str, product_name: str) -> float:
    """Token overlap: |commodity ∩ product| / |commodity|. Known limitation:
    'tomato' scores 1.0 against 'Tomato Ketchup'."""
    cw = _tokens(commodity_words)
    if not cw:
        return 0.0
    return len(cw & _tokens(product_name)) / len(cw)


# ---------------------------------------------------------------- client

@dataclass
class LivePack:
    commodity: str
    pack_size_g: float
    price_rs: float
    product_name: str
    score: float


class QuickCommerceClient:
    """Thin client over a quick-commerce product search API.
    Never called without an API key."""

    MATCH_THRESHOLD = 0.6

    def __init__(self, base_url: str, api_key: str, timeout: float = 6.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _search(self, query: str) -> list[dict]:
        resp = httpx.get(
            f"{self.base_url}/products/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("products", [])

    def fetch_packs(self, commodity: str, search_words: str,
                    piece_weight_g: float = 50.0) -> list[LivePack]:
        """Best-matching purchasable packs for one commodity. Any network or
        parse failure returns [] — the caller falls back to cache/seed."""
        try:
            products = self._search(search_words)
        except Exception:
            return []
        packs: list[LivePack] = []
        for p in products:
            name = str(p.get("name", ""))
            score = match_score(search_words, name)
            if score < self.MATCH_THRESHOLD:
                continue
            grams = parse_quantity_g(str(p.get("quantity", "")), piece_weight_g)
            price = p.get("price")
            if not grams or not isinstance(price, (int, float)) or price <= 0:
                continue
            packs.append(LivePack(commodity=commodity, pack_size_g=grams,
                                  price_rs=float(price), product_name=name, score=score))
        packs.sort(key=lambda x: (-x.score, x.price_rs / x.pack_size_g))
        return packs[:4]
