"""Load the WFP monthly retail price history into price_history.

The WFP series (1994-2026, monthly, many markets) trains the ARIMA/GARCH
volatility model in services/forecast.py. Current prices come from the
Agmarknet snapshot via scripts.seed — NOT from here; this file is history
only.

Aggregation: national median ₹/kg per commodity per month. Rows priced per
unusual units are skipped (the series used are all KG here).

Run:  cd backend && python -m scripts.ingest_prices
"""
from __future__ import annotations

import csv
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, engine   # noqa: E402
from app import models                          # noqa: E402
from scripts.ingredient_tables import WFP_MAP   # noqa: E402

WFP_CSV = Path(__file__).resolve().parents[2] / "dataset" / "wfp_food_prices_ind.csv"


def load_series(csv_path: Path) -> dict[tuple[str, date], float]:
    """(commodity, month) -> median price/kg across markets."""
    buckets: dict[tuple[str, date], list[float]] = {}
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            canon = WFP_MAP.get(row.get("commodity", ""))
            if not canon:
                continue
            unit = (row.get("unit") or "").upper()
            try:
                price = float(row["price"])
                y, m, _ = row["date"].split("-")
                month = date(int(y), int(m), 1)
            except (KeyError, ValueError):
                continue
            if price <= 0:
                continue
            if unit == "KG" or unit == "L":
                per_kg = price
            elif unit == "100 KG":
                per_kg = price / 100.0
            elif unit in ("DOZEN",):        # eggs: a dozen ~ 660 g
                per_kg = price / 0.66
            else:
                continue
            buckets.setdefault((canon, month), []).append(per_kg)
    return {k: statistics.median(v) for k, v in buckets.items()}


def main(quiet: bool = False) -> dict:
    if not WFP_CSV.exists():
        raise FileNotFoundError(WFP_CSV)
    Base.metadata.create_all(bind=engine)
    series = load_series(WFP_CSV)

    db = SessionLocal()
    try:
        db.query(models.PriceHistory).delete()
        db.commit()
        for (commodity, month), price in sorted(series.items()):
            db.add(models.PriceHistory(commodity=commodity, month=month,
                                       price_per_kg=round(price, 2)))
        db.commit()
    finally:
        db.close()

    commodities = {c for c, _ in series}
    stats = {"rows": len(series), "commodities": len(commodities)}
    if not quiet:
        print(f"Price history: {stats['rows']} monthly medians across "
              f"{stats['commodities']} commodities.")
    return stats


if __name__ == "__main__":
    main()
