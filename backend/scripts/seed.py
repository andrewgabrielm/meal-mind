"""Seed the database: 28 hand-structured recipes, pack prices, demo pantry.

Prices: the Agmarknet daily-snapshot CSV (dataset/) supplies CURRENT mandi
prices where available (modal ₹/quintal -> retail ₹/kg with a documented
markup); everything else falls back to the hand-seeded pack table below. The
seeded path is the backup and the testing path; it must keep working with no
key, no network and no CSV.

Also loads the WFP monthly price history (for the GARCH volatility model) via
scripts.ingest_prices when that CSV is present.

Run:  cd backend && python -m scripts.seed
"""
from __future__ import annotations

import csv
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, engine   # noqa: E402
from app import models                          # noqa: E402
from scripts.ingredient_tables import AGMARKNET_MAP, item_class  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parents[2] / "dataset"
AGMARKNET_CSV = DATASET_DIR / "9ef84268-d588-465a-a308-a864a43d0070.csv"

RETAIL_MARKUP = 1.4          # mandi (wholesale) -> retail shelf price
DEMO_USER = 1
DEMO_EMAIL = "demo@mealmind.app"
DEMO_PASSWORD = "demo1234"   # demo account only — owns the seeded pantry

# ---------------------------------------------------------------------------
# hand-seeded pack prices: commodity -> [(pack_size_g, price_rs)]
# Realistic Indian retail packs, 2026. Agmarknet overrides produce when present.
# ---------------------------------------------------------------------------
DEFAULT_PACKS: dict[str, list[tuple[float, float]]] = {
    "rice": [(1000, 62), (5000, 285)],
    "poha": [(500, 42)],
    "wheat_flour": [(1000, 52), (5000, 235)],
    "maida": [(500, 32)],
    "semolina": [(500, 38)],
    "besan": [(500, 62)],
    "millet": [(500, 52)],
    "toor_dal": [(500, 78), (1000, 148)],
    "moong_dal": [(500, 72), (1000, 138)],
    "chana_dal": [(500, 56), (1000, 105)],
    "urad_dal": [(500, 78)],
    "masoor_dal": [(500, 62)],
    "rajma": [(500, 88)],
    "chickpeas": [(500, 68)],
    "green_peas": [(250, 32), (500, 58)],
    "groundnut": [(250, 42)],
    "onion": [(500, 22), (1000, 40)],
    "tomato": [(500, 20), (1000, 36)],
    "potato": [(500, 18), (1000, 32)],
    "spinach": [(250, 25)],
    "fenugreek_leaves": [(250, 22)],
    "amaranthus": [(250, 20)],
    "cauliflower": [(500, 35)],
    "cabbage": [(500, 20), (1000, 36)],
    "brinjal": [(500, 26)],
    "okra": [(500, 32)],
    "bottle_gourd": [(500, 24)],
    "bitter_gourd": [(500, 36)],
    "ridge_gourd": [(500, 32)],
    "pumpkin": [(500, 20)],
    "carrot": [(500, 36)],
    "beans": [(500, 46)],
    "capsicum": [(250, 26)],
    "beetroot": [(500, 26)],
    "radish": [(500, 20)],
    "drumstick": [(250, 36)],
    "cucumber": [(500, 24)],
    "banana": [(700, 42)],
    "coconut": [(350, 40)],
    "ginger": [(100, 22)],
    "garlic": [(100, 28)],
    "green_chilli": [(100, 12)],
    "coriander_leaves": [(100, 15)],
    "mint": [(100, 15)],
    "curry_leaves": [(50, 10)],
    "lemon": [(200, 22)],
    "tamarind": [(200, 42)],
    "milk": [(500, 28), (1000, 55)],
    "curd": [(400, 42)],
    "paneer": [(200, 88)],
    "ghee": [(180, 125)],
    "butter": [(100, 58)],
    "egg": [(330, 46), (660, 88)],           # 6 / 12 eggs
    "chicken": [(500, 132), (1000, 245)],
    "mutton": [(500, 385)],
    "fish": [(500, 185)],
    "sunflower_oil": [(920, 132)],           # 1 L
    "mustard_oil": [(920, 152)],
    "salt": [(1000, 26)],
    "sugar": [(1000, 46)],
    "jaggery": [(500, 42)],
    "turmeric": [(100, 36)],
    "red_chilli_powder": [(100, 46)],
    "coriander_powder": [(100, 32)],
    "cumin_seeds": [(100, 42)],
    "mustard_seeds": [(100, 22)],
    "garam_masala": [(100, 62)],
    "amchur": [(100, 42)],
    "tea": [(250, 92)],
}

# ---------------------------------------------------------------------------
# 28 hand-structured recipes: (name, course, diet, region, mins, servings,
#                              [(commodity, grams)])
# ---------------------------------------------------------------------------
RECIPES: list[tuple] = [
    ("Kanda Poha", "breakfast", "vegetarian", "west", 20, 4, [
        ("poha", 250), ("onion", 150), ("potato", 100), ("groundnut", 30),
        ("mustard_seeds", 5), ("turmeric", 3), ("green_chilli", 10),
        ("sunflower_oil", 30), ("salt", 8), ("lemon", 20), ("coriander_leaves", 15),
        ("curry_leaves", 3)]),
    ("Rava Upma", "breakfast", "vegetarian", "south", 25, 4, [
        ("semolina", 250), ("onion", 100), ("carrot", 80), ("green_peas", 50),
        ("ginger", 10), ("green_chilli", 10), ("mustard_seeds", 4), ("urad_dal", 10),
        ("curry_leaves", 3), ("sunflower_oil", 35), ("salt", 8)]),
    ("Aloo Paratha", "breakfast", "vegetarian", "north", 40, 4, [
        ("wheat_flour", 300), ("potato", 400), ("onion", 60), ("green_chilli", 10),
        ("coriander_leaves", 15), ("cumin_seeds", 4), ("garam_masala", 5),
        ("salt", 10), ("ghee", 40), ("curd", 100)]),
    ("Idli with Coconut Chutney", "breakfast", "vegetarian", "south", 45, 4, [
        ("rice", 300), ("urad_dal", 100), ("coconut", 100), ("green_chilli", 8),
        ("curry_leaves", 2), ("mustard_seeds", 3), ("sunflower_oil", 10), ("salt", 10)]),
    ("Besan Chilla", "breakfast", "vegetarian", "north", 20, 4, [
        ("besan", 200), ("onion", 100), ("tomato", 100), ("green_chilli", 10),
        ("coriander_leaves", 15), ("turmeric", 3), ("sunflower_oil", 30), ("salt", 8)]),
    ("Moong Dal Cheela", "breakfast", "vegetarian", "north", 30, 4, [
        ("moong_dal", 250), ("ginger", 10), ("green_chilli", 10),
        ("coriander_leaves", 15), ("sunflower_oil", 30), ("salt", 8)]),
    ("Masala Egg Bhurji", "breakfast", "eggetarian", "west", 15, 4, [
        ("egg", 220), ("onion", 120), ("tomato", 100), ("green_chilli", 10),
        ("turmeric", 2), ("red_chilli_powder", 3), ("sunflower_oil", 25),
        ("salt", 6), ("coriander_leaves", 10)]),
    ("Masala Dosa", "breakfast", "vegetarian", "south", 60, 4, [
        ("rice", 300), ("urad_dal", 100), ("potato", 300), ("onion", 150),
        ("mustard_seeds", 4), ("turmeric", 3), ("curry_leaves", 3),
        ("sunflower_oil", 40), ("salt", 10)]),
    ("Dal Tadka", "lunch", "vegetarian", "north", 35, 4, [
        ("toor_dal", 250), ("onion", 100), ("tomato", 150), ("garlic", 15),
        ("cumin_seeds", 5), ("turmeric", 4), ("red_chilli_powder", 4),
        ("ghee", 25), ("salt", 10), ("coriander_leaves", 10)]),
    ("Palak Paneer", "dinner", "vegetarian", "north", 40, 4, [
        ("spinach", 500), ("paneer", 250), ("onion", 120), ("tomato", 100),
        ("garlic", 15), ("ginger", 10), ("garam_masala", 5), ("sunflower_oil", 30),
        ("salt", 10)]),
    ("Rajma Chawal", "lunch", "vegetarian", "north", 60, 4, [
        ("rajma", 250), ("rice", 300), ("onion", 200), ("tomato", 250),
        ("ginger", 15), ("garlic", 15), ("cumin_seeds", 5), ("garam_masala", 8),
        ("red_chilli_powder", 5), ("sunflower_oil", 35), ("salt", 10)]),
    ("Chole Masala", "lunch", "vegetarian", "north", 60, 4, [
        ("chickpeas", 250), ("onion", 200), ("tomato", 250), ("ginger", 15),
        ("garlic", 15), ("garam_masala", 8), ("amchur", 5), ("sunflower_oil", 35),
        ("salt", 10), ("coriander_leaves", 10)]),
    ("Aloo Gobi", "dinner", "vegetarian", "north", 35, 4, [
        ("potato", 300), ("cauliflower", 400), ("onion", 100), ("tomato", 100),
        ("turmeric", 4), ("cumin_seeds", 4), ("coriander_powder", 6),
        ("sunflower_oil", 35), ("salt", 10)]),
    ("Bhindi Masala", "dinner", "vegetarian", "north", 30, 4, [
        ("okra", 400), ("onion", 150), ("tomato", 100), ("amchur", 4),
        ("coriander_powder", 6), ("sunflower_oil", 35), ("salt", 8)]),
    ("Baingan Bharta", "dinner", "vegetarian", "north", 45, 4, [
        ("brinjal", 500), ("onion", 150), ("tomato", 200), ("garlic", 15),
        ("green_chilli", 10), ("sunflower_oil", 30), ("salt", 8),
        ("coriander_leaves", 15)]),
    ("Vegetable Pulao", "lunch", "vegetarian", "", 35, 4, [
        ("rice", 350), ("carrot", 100), ("beans", 100), ("green_peas", 100),
        ("onion", 120), ("ghee", 30), ("cumin_seeds", 5), ("garam_masala", 5),
        ("salt", 10)]),
    ("Curd Rice", "lunch", "vegetarian", "south", 20, 4, [
        ("rice", 300), ("curd", 400), ("milk", 100), ("mustard_seeds", 4),
        ("urad_dal", 8), ("curry_leaves", 3), ("ginger", 10), ("green_chilli", 8),
        ("sunflower_oil", 15), ("salt", 8)]),
    ("Drumstick Sambar", "dinner", "vegetarian", "south", 40, 4, [
        ("toor_dal", 200), ("drumstick", 150), ("pumpkin", 150), ("tomato", 100),
        ("tamarind", 20), ("garam_masala", 10), ("mustard_seeds", 4),
        ("curry_leaves", 3), ("sunflower_oil", 25), ("salt", 10)]),
    ("Tomato Rasam with Rice", "dinner", "vegetarian", "south", 30, 4, [
        ("rice", 300), ("tomato", 300), ("tamarind", 25), ("toor_dal", 30),
        ("garam_masala", 8), ("garlic", 10), ("cumin_seeds", 5),
        ("mustard_seeds", 3), ("curry_leaves", 3), ("sunflower_oil", 15), ("salt", 8)]),
    ("Moong Dal Khichdi", "dinner", "vegetarian", "", 30, 4, [
        ("rice", 200), ("moong_dal", 150), ("green_peas", 80), ("ghee", 25),
        ("cumin_seeds", 5), ("turmeric", 4), ("salt", 8)]),
    ("Matar Paneer", "dinner", "vegetarian", "north", 40, 4, [
        ("paneer", 250), ("green_peas", 200), ("onion", 150), ("tomato", 200),
        ("ginger", 10), ("garam_masala", 6), ("sunflower_oil", 35), ("salt", 10)]),
    ("Paneer Butter Masala", "dinner", "vegetarian", "north", 45, 4, [
        ("paneer", 300), ("tomato", 300), ("butter", 50), ("milk", 100),
        ("onion", 100), ("garam_masala", 6), ("red_chilli_powder", 4), ("salt", 8)]),
    ("Cabbage Thoran", "dinner", "vegetarian", "south", 25, 4, [
        ("cabbage", 500), ("coconut", 100), ("mustard_seeds", 4), ("urad_dal", 10),
        ("curry_leaves", 3), ("turmeric", 3), ("sunflower_oil", 25), ("salt", 8)]),
    ("Lauki Chana Dal", "dinner", "vegetarian", "north", 40, 4, [
        ("bottle_gourd", 400), ("chana_dal", 150), ("onion", 100), ("tomato", 100),
        ("turmeric", 4), ("cumin_seeds", 4), ("sunflower_oil", 30), ("salt", 8)]),
    ("Masala Karela", "dinner", "vegetarian", "north", 45, 4, [
        ("bitter_gourd", 400), ("onion", 150), ("besan", 45), ("turmeric", 5),
        ("red_chilli_powder", 5), ("cumin_seeds", 4), ("coriander_powder", 8),
        ("amchur", 8), ("sunflower_oil", 40), ("salt", 8)]),
    ("Egg Curry", "dinner", "eggetarian", "south", 35, 4, [
        ("egg", 275), ("onion", 200), ("tomato", 200), ("coconut", 80),
        ("garam_masala", 8), ("red_chilli_powder", 5), ("sunflower_oil", 35),
        ("salt", 8), ("curry_leaves", 3)]),
    ("Home-style Chicken Curry", "dinner", "non_vegetarian", "", 50, 4, [
        ("chicken", 800), ("onion", 250), ("tomato", 200), ("curd", 100),
        ("ginger", 20), ("garlic", 20), ("garam_masala", 10),
        ("red_chilli_powder", 6), ("turmeric", 5), ("sunflower_oil", 45), ("salt", 10)]),
    ("Meen Kulambu (Fish Curry)", "dinner", "non_vegetarian", "south", 40, 4, [
        ("fish", 600), ("tamarind", 30), ("coconut", 150), ("onion", 150),
        ("tomato", 150), ("curry_leaves", 3), ("mustard_seeds", 4),
        ("red_chilli_powder", 8), ("turmeric", 5), ("sunflower_oil", 40), ("salt", 8)]),
]

# cooking method per seeded recipe (ingested recipes carry their own from the CSV)
INSTRUCTIONS: dict[str, str] = {
    "Kanda Poha": "Rinse the poha in a colander and let it soften. Temper mustard seeds, curry leaves and green chilli in hot oil, fry the peanuts, then the sliced onion until translucent and the diced potato until cooked. Add turmeric and salt, fold in the poha, and steam covered for 2–3 minutes. Finish with lemon juice and coriander.",
    "Rava Upma": "Dry-roast the semolina until aromatic and set aside. Temper mustard seeds and urad dal in oil, sauté ginger, chilli and onion, then the carrot and peas. Pour in 2.5 cups of hot water with salt, and rain in the rava while stirring so no lumps form. Cover on low heat until fluffy.",
    "Aloo Paratha": "Knead a soft dough with the atta, salt and water; rest 20 minutes. Boil, mash and season the potatoes with green chilli, cumin, garam masala and coriander. Stuff balls of dough with the filling, roll gently, and roast on a hot tawa with ghee until golden-spotted on both sides. Serve with curd.",
    "Idli with Coconut Chutney": "Soak rice and urad dal separately for 4–6 hours, grind to a smooth batter and ferment overnight. Steam ladlefuls in an idli stand for 10–12 minutes. Grind coconut with green chilli and salt for the chutney, and temper it with mustard seeds and curry leaves.",
    "Besan Chilla": "Whisk besan with turmeric, salt and water to a pourable batter. Stir in chopped onion, tomato, chilli and coriander. Ladle onto a hot oiled tawa, spread thin, and cook both sides until golden and crisp at the edges.",
    "Moong Dal Cheela": "Soak the moong dal 3–4 hours and grind with ginger and green chilli to a smooth batter; season with salt. Spread ladlefuls thin on a hot oiled tawa and cook until the underside releases, flip and finish. Serve hot with chutney.",
    "Masala Egg Bhurji": "Heat oil and sauté onion until soft, then tomato and green chilli until pulpy. Add turmeric, chilli powder and salt, crack in the eggs and scramble on medium heat until just set. Finish with coriander; serve with pav or roti.",
    "Masala Dosa": "Use fermented rice–urad batter; spread a thin dosa on a hot greased tawa. For the filling, temper mustard seeds and curry leaves, sauté onion, add turmeric and the boiled crumbled potatoes with salt. Fill, fold and crisp the dosa with a little oil.",
    "Dal Tadka": "Pressure-cook the toor dal with turmeric until soft and whisk smooth. Sauté onion, garlic and tomato, add chilli powder and salt, and combine with the dal; simmer 5 minutes. Finish with a ghee tadka of cumin and garnish with coriander.",
    "Palak Paneer": "Blanch the spinach 2 minutes, refresh in cold water and purée. Sauté onion, ginger and garlic, add tomato and cook down, then the purée, garam masala and salt. Slide in the paneer cubes and simmer gently for 4–5 minutes.",
    "Rajma Chawal": "Soak rajma overnight and pressure-cook until butter-soft. Cook a masala of onion, ginger-garlic and tomato until the oil separates, add the spices, then the rajma with its stock; simmer until thick and creamy. Serve over steamed rice.",
    "Chole Masala": "Soak chickpeas overnight and pressure-cook until tender. Brown the onion well, add ginger-garlic, tomato and the masalas including amchur, and cook until glossy. Add the chole, mash a few for body, and simmer 10 minutes. Garnish with coriander.",
    "Aloo Gobi": "Sauté cumin in oil, add onion until golden, then turmeric and coriander powder. Add the potato first, fry 5 minutes, then the cauliflower florets and tomato with salt. Cover and cook on low, tossing gently, until both are tender but intact.",
    "Bhindi Masala": "Wash and thoroughly dry the bhindi, then slice. Fry it uncovered in hot oil until the sliminess cooks off; set aside. Sauté onion, add tomato and the spices, return the bhindi with amchur and salt, and toss on high heat until coated.",
    "Baingan Bharta": "Roast the whole brinjal over an open flame until the skin chars and the flesh collapses; peel and mash. Sauté garlic, green chilli and onion, add tomato and cook down, then the mash with salt. Cook until smoky and thick; finish with coriander.",
    "Vegetable Pulao": "Rinse and soak the rice 20 minutes. Sauté cumin and onion in ghee, add the carrot, beans and peas, then the drained rice, garam masala, salt and 1.75 cups water per cup of rice. Cook covered on low until every grain is separate.",
    "Curd Rice": "Cook the rice soft and cool to lukewarm; mash lightly with milk. Fold in whisked curd and salt. Temper mustard seeds, urad dal, ginger, green chilli and curry leaves in oil and pour over. Rest 10 minutes before serving.",
    "Drumstick Sambar": "Cook the toor dal soft. Simmer drumstick and pumpkin in tamarind water with salt until tender, add the dal and sambar powder, and boil 5 minutes. Temper mustard seeds and curry leaves in oil and pour over.",
    "Tomato Rasam with Rice": "Crush the tomatoes into tamarind water with rasam powder, crushed garlic and salt; add the cooked dal and simmer — never boil hard — until frothy at the edges. Temper cumin and mustard seeds, pour over, and serve with steamed rice.",
    "Moong Dal Khichdi": "Rinse rice and moong dal together. Sauté cumin in ghee, add the rice, dal, peas, turmeric and salt with 4 cups of water. Pressure-cook to a soft, spoonable consistency; loosen with hot water if needed and finish with more ghee.",
    "Matar Paneer": "Sauté onion and ginger, add tomato and cook to a thick masala with garam masala and salt. Add the peas with a splash of water and simmer until sweet, then the paneer cubes for a final 5 minutes.",
    "Paneer Butter Masala": "Cook onion and tomato down, cool, and blend to a silky purée. Return to the pan with butter, chilli powder and garam masala, add milk for creaminess, then the paneer. Simmer gently 5 minutes and finish with a knob of butter.",
    "Cabbage Thoran": "Shred the cabbage fine. Temper mustard seeds, urad dal and curry leaves in oil, add the cabbage with turmeric and salt, and stir-fry on high. Cover 3–4 minutes until just tender, then fold in the grated coconut off the heat.",
    "Lauki Chana Dal": "Soak chana dal an hour and pressure-cook until tender but whole. Sauté cumin and onion, add tomato and turmeric, then the cubed lauki; cook until translucent. Combine with the dal and simmer until the flavours meet.",
    "Masala Karela": "De-seed and slice the karela; pressure-cook briefly with turmeric and salt to tame the bitterness. Sauté cumin and sliced onion until golden, add the karela with besan, chilli, coriander powder and amchur, and roast uncovered until crisp-edged.",
    "Egg Curry": "Hard-boil, peel and lightly fry the eggs. Grind coconut to a paste. Sauté onion and curry leaves, add tomato and the spice powders, then the coconut paste with water; simmer to a thick gravy and slide in the halved eggs.",
    "Home-style Chicken Curry": "Marinate the chicken in curd, turmeric and salt for 30 minutes. Brown the onions well, add ginger-garlic, tomato and the ground spices, and cook until the oil separates. Add the chicken, sear, then simmer covered until cooked through and the gravy coats the pieces.",
    "Meen Kulambu (Fish Curry)": "Soak tamarind and extract the juice. Temper mustard seeds and curry leaves, sauté onion, add tomato, chilli and turmeric, then the tamarind water and ground coconut; boil 10 minutes. Slide in the fish and simmer gently 6–8 minutes without stirring.",
}

# demo pantry: (commodity, grams, storage, days_old). Deliberately includes
# aging perishables so the decay model and the 48-hour leftover ILP have
# something real to chew on.
DEMO_PANTRY = [
    ("spinach", 500, "fridge", 3),      # a full bunch, already 3 days old
    ("paneer", 250, "fridge", 2),
    ("cauliflower", 400, "fridge", 2),
    ("tomato", 300, "fridge", 4),
    ("curd", 200, "fridge", 2),
    ("milk", 500, "fridge", 1),
    ("green_peas", 250, "freezer", 10),
    ("potato", 500, "room", 5),
    ("onion", 700, "room", 4),
    ("rice", 2000, "room", 30),
    ("toor_dal", 400, "room", 20),
    ("moong_dal", 300, "room", 30),
    ("wheat_flour", 1500, "room", 15),
    ("sunflower_oil", 500, "room", 60),
]


def agmarknet_packs() -> dict[str, list[tuple[float, float]]]:
    """Current retail packs derived from the Agmarknet mandi snapshot:
    median modal price (₹/quintal) / 100 * markup = retail ₹/kg."""
    if not AGMARKNET_CSV.exists():
        return {}
    per_kg: dict[str, list[float]] = {}
    with AGMARKNET_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            canon = AGMARKNET_MAP.get(row["Commodity"])
            if not canon:
                continue
            try:
                modal = float(row["Modal_x0020_Price"])
            except (ValueError, KeyError):
                continue
            if modal <= 0:
                continue
            per_kg.setdefault(canon, []).append(modal / 100.0 * RETAIL_MARKUP)
    packs = {}
    for canon, prices in per_kg.items():
        kg = statistics.median(prices)
        if kg <= 0:
            continue
        # small packs carry a per-unit premium, as on real shelves
        packs[canon] = [(250.0, round(kg * 0.30, 1)), (500.0, round(kg * 0.55, 1)),
                        (1000.0, round(kg, 1))]
    return packs


def seed(db=None) -> dict:
    Base.metadata.create_all(bind=engine)
    own = db is None
    db = db or SessionLocal()
    try:
        # wipe seed-owned tables (idempotent reseeding)
        for model in (models.RecipeIngredient, models.Recipe, models.PricePack,
                      models.PantryItem):
            db.query(model).delete()
        db.commit()

        for name, course, diet, region, mins, servings, ingredients in RECIPES:
            r = models.Recipe(name=name, course=course, diet=diet, region=region,
                              time_mins=mins, servings=servings, source="seed",
                              instructions=INSTRUCTIONS.get(name, ""))
            db.add(r)
            db.flush()
            for commodity, grams in ingredients:
                db.add(models.RecipeIngredient(recipe_id=r.id, commodity=commodity,
                                               quantity_g=float(grams)))

        pack_table = dict(DEFAULT_PACKS)
        live_prices = agmarknet_packs()
        pack_table.update(live_prices)          # Agmarknet overrides produce
        for commodity, packs in pack_table.items():
            for size_g, price in packs:
                db.add(models.PricePack(commodity=commodity, pack_size_g=float(size_g),
                                        price_rs=float(price), source="seed"))

        # demo login owning the seeded pantry (id 1). Never overwrites an
        # existing account; other users are untouched by reseeding.
        from app.services.auth import hash_password
        if db.get(models.User, DEMO_USER) is None:
            db.add(models.User(id=DEMO_USER, email=DEMO_EMAIL, name="Demo Household",
                               password_hash=hash_password(DEMO_PASSWORD)))

        today = date.today()
        for commodity, grams, storage, days_old in DEMO_PANTRY:
            db.add(models.PantryItem(
                user_id=DEMO_USER, commodity=commodity, quantity_g=float(grams),
                storage=storage, purchased_on=today - timedelta(days=days_old),
                item_class=item_class(commodity),
            ))
        db.commit()

        stats = {
            "recipes": len(RECIPES),
            "priced_commodities": len(pack_table),
            "agmarknet_commodities": len(live_prices),
            "pantry_items": len(DEMO_PANTRY),
        }
        return stats
    finally:
        if own:
            db.close()


def main() -> None:
    stats = seed()
    print(f"Seeded {stats['recipes']} recipes, {stats['priced_commodities']} priced "
          f"commodities ({stats['agmarknet_commodities']} from the Agmarknet snapshot), "
          f"{stats['pantry_items']} demo pantry items.")
    print(f"Demo login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    # price history for the GARCH volatility model, when the WFP CSV is around
    try:
        from scripts.ingest_prices import main as ingest_prices_main
        ingest_prices_main(quiet=False)
    except FileNotFoundError:
        print("WFP price history CSV not found — volatility advisories disabled.")


if __name__ == "__main__":
    main()
