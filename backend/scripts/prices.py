"""Switch where prices come from, and see which tier is in use.

MealMind resolves each commodity's price in three tiers (services/pricing.py):

    1. live   QuickCommerce API      — only when QUICKCOMMERCE_API_KEY is set
    2. cache  price_packs (live)     — what a previous live fetch stored
    3. seed   price_packs (seed)     — Agmarknet-derived, always present

Tier 3 must keep working with no key, no network and no CSV — it is the
offline and testing path.

    python3 -m scripts.prices status      what is in use right now
    python3 -m scripts.prices live        switch on live prices (mock provider)
    python3 -m scripts.prices live --url https://api.example.com --key KEY
    python3 -m scripts.prices offline     switch back to seeded prices
    python3 -m scripts.prices clear-cache drop cached live prices

Restart the backend after switching — .env is read once at startup.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal        # noqa: E402
from app import models                 # noqa: E402

ENV = Path(__file__).resolve().parents[1] / ".env"
MOCK_URL = "http://localhost:9000"


def _set(key: str, value: str) -> None:
    text = ENV.read_text() if ENV.exists() else ""
    if re.search(rf"^{key}=.*$", text, re.M):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV.write_text(text)


def _get(key: str) -> str:
    if not ENV.exists():
        return ""
    m = re.search(rf"^{key}=(.*)$", ENV.read_text(), re.M)
    return m.group(1).strip() if m else ""


def _counts() -> dict[str, int]:
    db = SessionLocal()
    try:
        out: dict[str, int] = {}
        for row in db.query(models.PricePack).all():
            out[row.source] = out.get(row.source, 0) + 1
        return out
    finally:
        db.close()


def status() -> None:
    key, url = _get("QUICKCOMMERCE_API_KEY"), _get("QUICKCOMMERCE_BASE_URL")
    counts = _counts()
    print("Price source configuration")
    print(f"  live API   : {'ON  -> ' + url if key else 'off (no API key set)'}")
    print("\nPacks stored in the database")
    for src in ("live", "cache", "seed"):
        if counts.get(src):
            print(f"  {src:5s}: {counts[src]} packs"
                  + ("   <- cached live prices take precedence over seed" if src == "live" else ""))
    if not counts:
        print("  none — run `python3 -m scripts.seed` first")
    print("\nA plan's shopping_list reports the tier used per item as price_source.")
    if key:
        print("Live is ON. Start the provider if it is the mock: "
              "python3 -m scripts.mock_quickcommerce")
    print("Restart the backend after any change: ./run.sh")


def go_live(url: str, key: str) -> None:
    _set("QUICKCOMMERCE_API_KEY", key)
    _set("QUICKCOMMERCE_BASE_URL", url)
    print(f"Live prices ON -> {url}")
    if url == MOCK_URL:
        print("Start the mock provider in another terminal:")
        print("  cd backend && python3 -m scripts.mock_quickcommerce")
    print("Then restart the backend: ./run.sh")


def go_offline() -> None:
    _set("QUICKCOMMERCE_API_KEY", "")
    print("Live prices OFF — falling back to cache, then seeded prices.")
    if _counts().get("live"):
        print("NOTE: cached live prices still outrank seeded ones. To use pure")
        print("      seeded prices run: python3 -m scripts.prices clear-cache")
    print("Restart the backend: ./run.sh")


def clear_cache() -> None:
    db = SessionLocal()
    try:
        n = db.query(models.PricePack).filter(models.PricePack.source == "live").delete()
        db.commit()
        print(f"Cleared {n} cached live packs. Seeded prices are now in use.")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    live = sub.add_parser("live")
    live.add_argument("--url", default=MOCK_URL)
    live.add_argument("--key", default="mock-key")
    sub.add_parser("offline")
    sub.add_parser("clear-cache")
    args = ap.parse_args()

    if args.cmd == "live":
        go_live(args.url, args.key)
    elif args.cmd == "offline":
        go_offline()
    elif args.cmd == "clear-cache":
        clear_cache()
    else:
        status()


if __name__ == "__main__":
    main()
