"""A stand-in quick-commerce API, so the live-price path can be exercised.

No Indian quick-commerce platform (Blinkit, Zepto, Instamart, BigBasket)
publishes a public product API, so `services/quickcommerce.py` was written
against a generic contract and had never been called for real. This server
implements that contract from the seeded price table, with a small markup and
a little jitter so live prices are visibly different from seeded ones.

Use it to demonstrate that step 5 of the pipeline really does prefer live
prices, and that they get written to the cache.

    Terminal A:  cd backend && python3 -m scripts.mock_quickcommerce
    Terminal B:  edit backend/.env ->
                     QUICKCOMMERCE_API_KEY=any-non-empty-string
                     QUICKCOMMERCE_BASE_URL=http://localhost:9000
                 then restart ./run.sh and generate a plan.

Shopping-list rows should now report price_source "live" instead of "seed".

THE CONTRACT (what a real provider must be adapted to, in `_search`):
    GET {BASE_URL}/products/search?q=<words>
    Authorization: Bearer <API key>
    -> {"products": [{"name": str, "quantity": "500 g", "price": 62.0}, ...]}
`quantity` is free text — the parser understands g/kg/ml/L/pcs/dozen and
"2 x 250g". Anything it cannot parse is skipped, not guessed.
"""
from __future__ import annotations

import json
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal          # noqa: E402
from app import models                   # noqa: E402

PORT = 9000
MARKUP = 1.08          # quick commerce charges a convenience premium
BRANDS = ["Fresho", "Daily Good", "Farm Select", "Kitchen Basics"]


def catalogue() -> dict[str, list[dict]]:
    """Build a product catalogue from the seeded packs."""
    db = SessionLocal()
    try:
        rows = db.query(models.PricePack).filter(models.PricePack.source == "seed").all()
        out: dict[str, list[dict]] = {}
        rng = random.Random(7)                      # stable across restarts
        for r in rows:
            words = r.commodity.replace("_", " ")
            size = (f"{r.pack_size_g / 1000:g} kg" if r.pack_size_g >= 1000
                    else f"{r.pack_size_g:g} g")
            out.setdefault(words, []).append({
                "name": f"{rng.choice(BRANDS)} {words.title()}",
                "quantity": size,
                "price": round(r.price_rs * MARKUP * rng.uniform(0.94, 1.06), 1),
            })
        return out
    finally:
        db.close()


CATALOGUE = catalogue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        url = urlparse(self.path)
        if url.path != "/products/search":
            return self._json({"error": "not found"}, 404)
        if not (self.headers.get("Authorization") or "").startswith("Bearer "):
            return self._json({"error": "missing api key"}, 401)

        q = (parse_qs(url.query).get("q") or [""])[0].lower().strip()
        products = CATALOGUE.get(q, [])
        if not products:                                 # loose fallback match
            for words, items in CATALOGUE.items():
                if q and (q in words or words in q):
                    products = items
                    break
        print(f"  search {q!r} -> {len(products)} products")
        self._json({"products": products})

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                        # quiet default logging
        pass


def main() -> None:
    if not CATALOGUE:
        sys.exit("No seeded prices found — run `python3 -m scripts.seed` first.")
    print(f"Mock quick-commerce API on http://localhost:{PORT}")
    print(f"  {len(CATALOGUE)} commodities, "
          f"{sum(len(v) for v in CATALOGUE.values())} products")
    print("  Point backend/.env at it:")
    print("    QUICKCOMMERCE_API_KEY=mock-key")
    print(f"    QUICKCOMMERCE_BASE_URL=http://localhost:{PORT}")
    print("  Ctrl+C stops.")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
