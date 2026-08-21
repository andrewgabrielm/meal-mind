"""Ingest the recipe CSV (Archana's Kitchen / Kaggle "IndianFoodDatasetCSV",
the file in dataset/) into the recipes / recipe_ingredients tables.

Tuning lives in scripts/ingredient_tables.py — edit THAT, never this file,
when coverage needs improving.

Usage:
    python -m scripts.ingest_recipes --dry-run       # parse + report only
    python -m scripts.ingest_recipes                 # replace recipe tables
    python -m scripts.ingest_recipes --keep-seed     # append to the seeded 28

A recipe converts when >= 60% of its measurable ingredient lines map to a
canonical commodity, at least 3 distinct commodities emerge, and total mass
is plausible for the servings. Expect 60-75% conversion on a real run.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, engine     # noqa: E402
from app import models                            # noqa: E402
from scripts.ingredient_tables import (           # noqa: E402
    MASS_G, MEASURE_ML, TO_TASTE, canonical_for, density, is_indian_cuisine,
    unit_weight,
)

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_CSVS = [
    BACKEND / "data" / "recipes_raw.csv",
    *sorted((BACKEND.parent / "dataset").glob("*FoodDataset*.csv")),
]

MIN_LINE_COVERAGE = 0.6
MIN_COMMODITIES = 3
MIN_TOTAL_G = 250.0
MAX_TOTAL_G = 8000.0

COURSE_MAP = {
    "breakfast": "breakfast", "north indian breakfast": "breakfast",
    "south indian breakfast": "breakfast", "world breakfast": "breakfast",
    "indian breakfast": "breakfast",
    "lunch": "lunch", "main course": "dinner", "dinner": "dinner",
    "one pot dish": "dinner", "high protein vegetarian": "lunch",
    "brunch": "breakfast",
    # misfiled diet labels that appear in the Course column
    "vegetarian": "lunch", "vegan": "lunch", "eggetarian": "lunch",
    "non vegeterian": "lunch", "no onion no garlic (sattvic)": "lunch",
    "sugar free diet": "lunch",
}

DIET_MAP = {
    "vegetarian": "vegetarian", "high protein vegetarian": "vegetarian",
    "diabetic friendly": "vegetarian", "gluten free": "vegetarian",
    "no onion no garlic (sattvic)": "vegetarian", "sugar free diet": "vegetarian",
    "vegan": "vegan", "eggetarian": "eggetarian",
    "high protein non vegetarian": "non_vegetarian",
    "non vegeterian": "non_vegetarian", "non vegetarian": "non_vegetarian",
}

REGION_MAP = {
    "south": ["south indian", "tamil nadu", "kerala", "andhra", "karnataka",
              "chettinad", "udupi", "hyderabadi", "mangalorean"],
    "north": ["north indian", "punjabi", "rajasthani", "himachal", "awadhi",
              "kashmiri", "uttar pradesh", "lucknowi", "haryana", "delhi", "mughlai"],
    "east": ["bengali", "oriya", "odia", "assamese", "north east", "bihari",
             "jharkhand", "sikkimese"],
    "west": ["maharashtrian", "gujarati", "goan", "konkan", "malvani",
             "parsi", "sindhi"],
}

_NUM_RE = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(.*)$")


def _to_float(num: str) -> float:
    num = num.strip()
    if " " in num:                       # "1 1/2"
        whole, frac = num.split()
        a, b = frac.split("/")
        return float(whole) + float(a) / float(b)
    if "/" in num:                       # "1/2"
        a, b = num.split("/")
        return float(a) / float(b)
    return float(num)


def parse_line(line: str) -> tuple[str, float] | None | str:
    """One ingredient line -> (commodity, grams), None (unparseable),
    or "skip" (garnish/to-taste line that shouldn't count against coverage)."""
    text = line.strip()
    if not text:
        return "skip"
    low = text.lower()
    canon = canonical_for(text)
    if any(t in low for t in TO_TASTE):
        # salt to taste / oil as required: nominal amounts, never a failure
        if canon:
            return (canon, 8.0)
        return "skip"
    if canon is None:
        return None

    m = _NUM_RE.match(text)
    if not m:
        return (canon, unit_weight(canon))          # "Salt", "A pinch of hing"
    qty = _to_float(m.group(1))
    rest = m.group(2).lower()
    first_word = re.split(r"[^a-z]+", rest.strip() + " ")[0]

    if first_word in MASS_G:
        grams = qty * MASS_G[first_word]
    elif first_word in MEASURE_ML:
        grams = qty * MEASURE_ML[first_word] * density(canon)
    else:                                           # "6 Karela", "2 Onions"
        grams = qty * unit_weight(canon)
    if grams <= 0:
        return None
    return (canon, min(grams, 3000.0))


def map_course(course: str) -> str | None:
    return COURSE_MAP.get((course or "").strip().lower())


def map_region(cuisine: str) -> str:
    low = (cuisine or "").lower()
    for region, needles in REGION_MAP.items():
        if any(n in low for n in needles):
            return region
    return ""


def infer_diet(csv_diet: str, commodities: set[str]) -> str:
    if {"chicken", "mutton", "fish"} & commodities:
        return "non_vegetarian"
    if "egg" in commodities:
        return "eggetarian"
    mapped = DIET_MAP.get((csv_diet or "").strip().lower())
    if mapped in ("vegan", "vegetarian", "eggetarian"):
        return mapped
    return "vegetarian"


def parse_recipe(row: dict, indian_only: bool = True) -> tuple[dict | None, str]:
    """Returns (recipe dict or None, reason)."""
    course = map_course(row.get("Course", ""))
    if course is None:
        return None, "course"
    if indian_only and not is_indian_cuisine(row.get("Cuisine", "")):
        return None, "cuisine"

    raw = row.get("TranslatedIngredients") or row.get("Ingredients") or ""
    lines = [l for l in raw.split(",") if l.strip()]
    if not lines:
        return None, "no_ingredients"

    parsed: dict[str, float] = {}
    countable = 0
    hits = 0
    for line in lines:
        result = parse_line(line)
        if result == "skip":
            continue
        countable += 1
        if result is None:
            continue
        hits += 1
        c, g = result
        parsed[c] = parsed.get(c, 0.0) + g

    if countable == 0 or hits / countable < MIN_LINE_COVERAGE:
        return None, "coverage"
    if len(parsed) < MIN_COMMODITIES:
        return None, "too_few_commodities"
    total = sum(parsed.values())
    if not (MIN_TOTAL_G <= total <= MAX_TOTAL_G):
        return None, "implausible_mass"

    try:
        servings = max(1, min(12, int(float(row.get("Servings") or 4))))
    except ValueError:
        servings = 4
    try:
        mins = max(5, min(240, int(float(row.get("TotalTimeInMins") or 30))))
    except ValueError:
        mins = 30

    return {
        "name": (row.get("TranslatedRecipeName") or row.get("RecipeName") or "").strip()[:200],
        "course": course,
        "diet": infer_diet(row.get("Diet", ""), set(parsed)),
        "region": map_region(row.get("Cuisine", "")),
        "time_mins": mins,
        "servings": servings,
        "source_url": (row.get("URL") or "").strip()[:400],
        "instructions": (row.get("TranslatedInstructions")
                         or row.get("Instructions") or "").strip(),
        "ingredients": parsed,
    }, "ok"


def run(csv_path: Path, dry_run: bool, keep_seed: bool, limit: int | None,
        indian_only: bool = True) -> dict:
    reasons: dict[str, int] = {}
    accepted: list[dict] = []
    with csv_path.open(encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if limit and i >= limit:
                break
            recipe, reason = parse_recipe(row, indian_only)
            reasons[reason] = reasons.get(reason, 0) + 1
            if recipe:
                accepted.append(recipe)

    total = sum(reasons.values())
    stats = {"rows": total, "accepted": len(accepted),
             "conversion": round(len(accepted) / total, 3) if total else 0.0,
             "rejected": {k: v for k, v in sorted(reasons.items()) if k != "ok"}}

    if dry_run:
        return stats

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not keep_seed:
            db.query(models.RecipeIngredient).delete()
            db.query(models.Recipe).delete()
            db.commit()
        for r in accepted:
            rec = models.Recipe(
                name=r["name"], course=r["course"], diet=r["diet"],
                region=r["region"], time_mins=r["time_mins"], servings=r["servings"],
                source_url=r["source_url"], instructions=r["instructions"],
                source="ingest",
            )
            db.add(rec)
            db.flush()
            for c, g in r["ingredients"].items():
                db.add(models.RecipeIngredient(recipe_id=rec.id, commodity=c,
                                               quantity_g=round(g, 1)))
        db.commit()
    finally:
        db.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="parse and report only")
    ap.add_argument("--keep-seed", action="store_true", help="append to the seeded 28")
    ap.add_argument("--limit", type=int, default=None, help="parse only the first N rows")
    ap.add_argument("--world", action="store_true",
                    help="also ingest non-Indian cuisines (default: Indian only)")
    args = ap.parse_args()

    csv_path = args.csv or next((p for p in DEFAULT_CSVS if p.exists()), None)
    if csv_path is None or not csv_path.exists():
        sys.exit("recipe CSV not found — pass --csv PATH")

    stats = run(csv_path, args.dry_run, args.keep_seed, args.limit,
                indian_only=not args.world)
    mode = "DRY RUN — nothing written" if args.dry_run else \
           ("appended" if args.keep_seed else "replaced recipe tables")
    print(f"{csv_path.name}: {stats['rows']} rows, {stats['accepted']} converted "
          f"({stats['conversion']:.1%}). {mode}.")
    if stats["rejected"]:
        print("rejections:", ", ".join(f"{k}={v}" for k, v in stats["rejected"].items()))


if __name__ == "__main__":
    main()
