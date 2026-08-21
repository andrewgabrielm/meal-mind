"""Measure, density, alias and classification tables.

THIS is the file to edit when ingest coverage or commodity behaviour needs
tuning — never ingest_recipes.py. Everything here is plain data.

Commodity metadata keys:
  cls       decay item class (see services/decay.py CLASS_PARAMS)
  cluster   dish-family cluster for variety enforcement
  protein   True if the commodity pulls a dish into its cluster with
            precedence over heavier vegetables (curd/milk are NOT proteins
            here by design — a side of curd is not a dairy dish)
  staple    True = ignored when picking the dominant-mass cluster
  optional  True = the purchase ILP may drop it to fit the budget
  unit_g    weight of one piece (for "2 onions" style lines)
  density   g/ml for volume measures
"""

# ---------------------------------------------------------------------------
# volume measures (ml). Applied as qty * ml * density.
# ---------------------------------------------------------------------------
MEASURE_ML = {
    "teaspoon": 5.0, "teaspoons": 5.0, "tsp": 5.0,
    "tablespoon": 15.0, "tablespoons": 15.0, "tbsp": 15.0, "tbspn": 15.0,
    "cup": 240.0, "cups": 240.0,
    "glass": 250.0, "katori": 150.0, "bowl": 250.0,
    "ml": 1.0, "millilitre": 1.0, "milliliter": 1.0,
    "litre": 1000.0, "liter": 1000.0, "l": 1000.0,
    "pinch": 0.3, "sprig": 2.0, "handful": 30.0,
}

MASS_G = {
    "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
}

# words meaning "unmeasurable garnish" — ingest treats the line as optional
TO_TASTE = {"to taste", "as required", "as needed", "for garnish", "a few"}

DEFAULT_DENSITY = 0.7   # dry-ish powders/grains when nothing better is known
DEFAULT_UNIT_G = 50.0   # unknown "1 piece"

# ---------------------------------------------------------------------------
# canonical commodities
# ---------------------------------------------------------------------------
COMMODITY_INFO: dict[str, dict] = {
    # staples / grains
    "rice":          {"cls": "staple_dry", "cluster": "rice_dish", "staple": True, "density": 0.85, "unit_g": 200},
    "poha":          {"cls": "staple_dry", "cluster": "rice_dish", "staple": True, "density": 0.35},
    "wheat_flour":   {"cls": "staple_dry", "cluster": "wheat_bread", "staple": True, "density": 0.55},
    "maida":         {"cls": "staple_dry", "cluster": "wheat_bread", "staple": True, "density": 0.55},
    "semolina":      {"cls": "staple_dry", "cluster": "wheat_bread", "staple": True, "density": 0.7},
    "besan":         {"cls": "staple_dry", "cluster": "legume", "staple": True, "density": 0.5},
    "millet":        {"cls": "staple_dry", "cluster": "rice_dish", "staple": True, "density": 0.8},

    # pulses (proteins with lowest precedence)
    "toor_dal":      {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.85},
    "moong_dal":     {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.85},
    "chana_dal":     {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.85},
    "urad_dal":      {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.85},
    "masoor_dal":    {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.85},
    "rajma":         {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.8},
    "chickpeas":     {"cls": "staple_dry", "cluster": "legume", "protein": True, "density": 0.8},
    "green_peas":    {"cls": "vegetable", "cluster": "legume", "protein": True, "density": 0.6, "unit_g": 100},
    "groundnut":     {"cls": "staple_dry", "cluster": "legume", "density": 0.65},

    # vegetables
    "onion":         {"cls": "root_vegetable", "cluster": "mixed_veg", "unit_g": 110},
    "tomato":        {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 90},
    "potato":        {"cls": "root_vegetable", "cluster": "potato", "unit_g": 120},
    "spinach":       {"cls": "leafy_green", "cluster": "leafy_green", "unit_g": 250, "density": 0.25},
    "fenugreek_leaves": {"cls": "leafy_green", "cluster": "leafy_green", "unit_g": 150, "density": 0.25},
    "amaranthus":    {"cls": "leafy_green", "cluster": "leafy_green", "unit_g": 200, "density": 0.25},
    "cauliflower":   {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 400},
    "cabbage":       {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 500},
    "brinjal":       {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 100},
    "okra":          {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 15},
    "bottle_gourd":  {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 500},
    "bitter_gourd":  {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 80},
    "ridge_gourd":   {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 150},
    "pumpkin":       {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 500},
    "carrot":        {"cls": "root_vegetable", "cluster": "mixed_veg", "unit_g": 60},
    "beans":         {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 5},
    "capsicum":      {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 120},
    "beetroot":      {"cls": "root_vegetable", "cluster": "mixed_veg", "unit_g": 100},
    "radish":        {"cls": "root_vegetable", "cluster": "mixed_veg", "unit_g": 150},
    "drumstick":     {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 60},
    "cucumber":      {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 150},
    "banana":        {"cls": "fruit", "cluster": "mixed_veg", "unit_g": 120},
    "coconut":       {"cls": "vegetable", "cluster": "mixed_veg", "unit_g": 350, "density": 0.45},

    # aromatics / garnish (optional by design)
    "ginger":        {"cls": "root_vegetable", "cluster": "mixed_veg", "optional": True, "unit_g": 15, "density": 0.6},
    "garlic":        {"cls": "root_vegetable", "cluster": "mixed_veg", "optional": True, "unit_g": 5, "density": 0.6},
    "green_chilli":  {"cls": "vegetable", "cluster": "mixed_veg", "optional": True, "unit_g": 5},
    "coriander_leaves": {"cls": "leafy_green", "cluster": "leafy_green", "optional": True, "staple": True, "unit_g": 25, "density": 0.2},
    "mint":          {"cls": "leafy_green", "cluster": "leafy_green", "optional": True, "staple": True, "unit_g": 25, "density": 0.2},
    "curry_leaves":  {"cls": "leafy_green", "cluster": "leafy_green", "optional": True, "staple": True, "unit_g": 5, "density": 0.2},
    "lemon":         {"cls": "fruit", "cluster": "mixed_veg", "optional": True, "unit_g": 50},
    "tamarind":      {"cls": "spice", "cluster": "mixed_veg", "optional": True, "unit_g": 20, "density": 0.8},

    # dairy & animal proteins
    "milk":          {"cls": "dairy_fresh", "cluster": "dairy", "staple": True, "density": 1.03},
    "curd":          {"cls": "dairy_fresh", "cluster": "dairy", "staple": True, "density": 1.02},
    "paneer":        {"cls": "dairy_fresh", "cluster": "paneer", "protein": True, "unit_g": 200},
    "ghee":          {"cls": "oil_fat", "cluster": "dairy", "staple": True, "optional": True, "density": 0.9},
    "butter":        {"cls": "dairy_fresh", "cluster": "dairy", "staple": True, "optional": True, "density": 0.95},
    "egg":           {"cls": "egg", "cluster": "egg", "protein": True, "unit_g": 55},
    "chicken":       {"cls": "meat_fresh", "cluster": "chicken", "protein": True, "unit_g": 500},
    "mutton":        {"cls": "meat_fresh", "cluster": "mutton", "protein": True, "unit_g": 500},
    "fish":          {"cls": "meat_fresh", "cluster": "fish", "protein": True, "unit_g": 400},

    # oils, sweeteners, spices (all staples; most optional for the ILP)
    "sunflower_oil": {"cls": "oil_fat", "cluster": "mixed_veg", "staple": True, "density": 0.92},
    "mustard_oil":   {"cls": "oil_fat", "cluster": "mixed_veg", "staple": True, "density": 0.92},
    "salt":          {"cls": "spice", "cluster": "mixed_veg", "staple": True, "density": 1.2},
    "sugar":         {"cls": "staple_dry", "cluster": "mixed_veg", "staple": True, "density": 0.85},
    "jaggery":       {"cls": "staple_dry", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.9},
    "turmeric":      {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.6},
    "red_chilli_powder": {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.5},
    "coriander_powder":  {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.5},
    "cumin_seeds":   {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.6},
    "mustard_seeds": {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.7},
    "garam_masala":  {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.5},
    "amchur":        {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.5},
    "tea":           {"cls": "spice", "cluster": "mixed_veg", "staple": True, "optional": True, "density": 0.4},
}

# convenience sets derived from the table
PROTEIN_COMMODITIES = {k for k, v in COMMODITY_INFO.items() if v.get("protein")}
STAPLE_COMMODITIES = {k for k, v in COMMODITY_INFO.items() if v.get("staple")}
OPTIONAL_COMMODITIES = {k for k, v in COMMODITY_INFO.items() if v.get("optional")}

# protein precedence when several proteins share a dish (higher wins)
PROTEIN_PRECEDENCE = ["mutton", "chicken", "fish", "egg", "paneer",
                      "rajma", "chickpeas", "toor_dal", "chana_dal",
                      "moong_dal", "urad_dal", "masoor_dal", "green_peas"]

ALL_CLUSTERS = sorted({v["cluster"] for v in COMMODITY_INFO.values()})

# clusters unreachable for a diet (used for attainable-K normalisation)
DIET_EXCLUDED_CLUSTERS = {
    "vegetarian": {"egg", "chicken", "mutton", "fish"},
    "vegan": {"egg", "chicken", "mutton", "fish", "paneer", "dairy"},
    "eggetarian": {"chicken", "mutton", "fish"},
    "non_vegetarian": set(),
}

# ---------------------------------------------------------------------------
# aliases: lowercase token phrase -> canonical commodity. Longest match wins.
# Covers the Archana's Kitchen CSV vocabulary (English + transliterations).
# ---------------------------------------------------------------------------
ALIASES: dict[str, str] = {
    # grains
    "rice": "rice", "basmati": "rice", "cooked rice": "rice", "idli rice": "rice",
    "poha": "poha", "flattened rice": "poha", "aval": "poha",
    "whole wheat flour": "wheat_flour", "wheat flour": "wheat_flour", "atta": "wheat_flour",
    "all purpose flour": "maida", "maida": "maida", "plain flour": "maida",
    "semolina": "semolina", "sooji": "semolina", "rava": "semolina",
    "gram flour": "besan", "besan": "besan", "chickpea flour": "besan",
    "ragi": "millet", "bajra": "millet", "jowar": "millet", "foxtail millet": "millet",
    # pulses
    "toor dal": "toor_dal", "arhar dal": "toor_dal", "arhar": "toor_dal",
    "tur dal": "toor_dal", "pigeon pea": "toor_dal",
    "moong dal": "moong_dal", "yellow moong": "moong_dal", "green moong": "moong_dal",
    "mung": "moong_dal", "green gram": "moong_dal",
    "chana dal": "chana_dal", "bengal gram": "chana_dal",
    "urad dal": "urad_dal", "black gram": "urad_dal", "black urad": "urad_dal",
    "masoor dal": "masoor_dal", "red lentil": "masoor_dal", "masoor": "masoor_dal",
    "rajma": "rajma", "kidney bean": "rajma",
    "kabuli chana": "chickpeas", "chickpea": "chickpeas", "chole": "chickpeas",
    "white chana": "chickpeas", "garbanzo": "chickpeas", "kala chana": "chickpeas",
    "green peas": "green_peas", "peas": "green_peas", "matar": "green_peas",
    "peanut": "groundnut", "groundnut": "groundnut", "raw peanut": "groundnut",
    # vegetables
    "onion": "onion", "pyaz": "onion", "shallot": "onion", "spring onion": "onion",
    "tomato": "tomato", "tamatar": "tomato",
    "potato": "potato", "aloo": "potato", "baby potato": "potato",
    "spinach": "spinach", "palak": "spinach",
    "methi leaves": "fenugreek_leaves", "fenugreek leaves": "fenugreek_leaves", "methi": "fenugreek_leaves",
    "amaranth": "amaranthus", "amaranthus": "amaranthus",
    "cauliflower": "cauliflower", "gobi": "cauliflower", "phool gobi": "cauliflower",
    "cabbage": "cabbage", "patta gobi": "cabbage",
    "brinjal": "brinjal", "eggplant": "brinjal", "baingan": "brinjal", "aubergine": "brinjal",
    "bhindi": "okra", "okra": "okra", "ladies finger": "okra", "lady finger": "okra",
    "bottle gourd": "bottle_gourd", "lauki": "bottle_gourd", "doodhi": "bottle_gourd", "sorakkai": "bottle_gourd",
    "bitter gourd": "bitter_gourd", "karela": "bitter_gourd", "pavakkai": "bitter_gourd",
    "ridge gourd": "ridge_gourd", "turai": "ridge_gourd", "tori": "ridge_gourd",
    "pumpkin": "pumpkin", "kaddu": "pumpkin",
    "carrot": "carrot", "gajar": "carrot",
    "green beans": "beans", "french beans": "beans", "beans": "beans", "cluster beans": "beans",
    "capsicum": "capsicum", "bell pepper": "capsicum", "shimla mirch": "capsicum",
    "beetroot": "beetroot", "radish": "radish", "mooli": "radish",
    "drumstick": "drumstick", "moringa": "drumstick",
    "cucumber": "cucumber", "kheera": "cucumber",
    "banana": "banana", "raw banana": "banana", "plantain": "banana",
    "coconut": "coconut", "fresh coconut": "coconut", "grated coconut": "coconut", "coconut milk": "coconut",
    # aromatics
    "ginger": "ginger", "adrak": "ginger", "ginger garlic paste": "ginger",
    "garlic": "garlic", "lehsun": "garlic",
    "green chilli": "green_chilli", "green chillies": "green_chilli", "green chili": "green_chilli",
    "coriander leaves": "coriander_leaves", "cilantro": "coriander_leaves",
    "fresh coriander": "coriander_leaves", "dhania leaves": "coriander_leaves",
    "mint leaves": "mint", "pudina": "mint",
    "curry leaves": "curry_leaves", "kadi patta": "curry_leaves",
    "lemon": "lemon", "lime": "lemon", "lemon juice": "lemon",
    "tamarind": "tamarind", "imli": "tamarind",
    # dairy & proteins
    "milk": "milk", "doodh": "milk",
    "curd": "curd", "yogurt": "curd", "yoghurt": "curd", "dahi": "curd", "hung curd": "curd",
    "paneer": "paneer", "cottage cheese": "paneer",
    "ghee": "ghee", "clarified butter": "ghee",
    "butter": "butter",
    "egg": "egg", "eggs": "egg",
    "chicken": "chicken", "chicken breast": "chicken", "boneless chicken": "chicken",
    "mutton": "mutton", "lamb": "mutton", "goat": "mutton",
    # longest-match wins: stops "goat cheese" reading as mutton
    "goat cheese": "paneer", "cheese": "paneer",
    "fish": "fish", "prawn": "fish", "shrimp": "fish", "pomfret": "fish", "surmai": "fish",
    # oil / sweet / spice
    "sunflower oil": "sunflower_oil", "oil": "sunflower_oil", "vegetable oil": "sunflower_oil",
    "refined oil": "sunflower_oil", "cooking oil": "sunflower_oil", "sesame oil": "sunflower_oil",
    "coconut oil": "sunflower_oil", "olive oil": "sunflower_oil",
    "mustard oil": "mustard_oil",
    "salt": "salt", "rock salt": "salt", "black salt": "salt",
    "sugar": "sugar", "caster sugar": "sugar",
    "jaggery": "jaggery", "gur": "jaggery",
    "turmeric": "turmeric", "haldi": "turmeric", "turmeric powder": "turmeric",
    "red chilli powder": "red_chilli_powder", "red chili powder": "red_chilli_powder",
    "chilli powder": "red_chilli_powder", "lal mirch": "red_chilli_powder",
    "kashmiri red chilli": "red_chilli_powder", "dry red chilli": "red_chilli_powder",
    "red chillies": "red_chilli_powder",
    "coriander powder": "coriander_powder", "dhania powder": "coriander_powder",
    "coriander seeds": "coriander_powder",
    "cumin seeds": "cumin_seeds", "jeera": "cumin_seeds", "cumin powder": "cumin_seeds", "cumin": "cumin_seeds",
    "mustard seeds": "mustard_seeds", "rai": "mustard_seeds",
    "garam masala": "garam_masala", "sambar powder": "garam_masala", "rasam powder": "garam_masala",
    "chaat masala": "garam_masala", "pav bhaji masala": "garam_masala", "biryani masala": "garam_masala",
    "curry powder": "garam_masala", "kitchen king masala": "garam_masala",
    "amchur": "amchur", "dry mango powder": "amchur", "amchoor": "amchur",
    "tea": "tea", "tea leaves": "tea", "chai": "tea",
}

# Indian cuisines in the Archana's Kitchen CSV (lowercase, normalised).
# Ingest keeps ONLY these unless --world is passed. "Indo Chinese" stays —
# it is Indian street food; "Fusion"/"Continental" are excluded as the corpus
# must stay recognisably Indian by default.
INDIAN_CUISINES = {
    "indian", "north indian recipes", "south indian recipes",
    "bengali recipes", "maharashtrian recipes", "kerala recipes",
    "tamil nadu", "karnataka", "rajasthani", "andhra", "gujarati recipes",
    "goan recipes", "punjabi", "chettinad", "kashmiri", "mangalorean",
    "parsi recipes", "indo chinese", "awadhi", "oriya recipes", "sindhi",
    "konkan", "mughlai", "bihari", "hyderabadi", "assamese",
    "north east india recipes", "himachal", "udupi", "coorg",
    "coastal karnataka", "north karnataka", "south karnataka",
    "uttar pradesh", "lucknowi", "malabar", "malvani", "nagaland",
    "uttarakhand-north kumaon", "haryana", "kongunadu", "jharkhand",
}


def is_indian_cuisine(cuisine: str) -> bool:
    return cuisine.replace("﻿", "").strip().lower() in INDIAN_CUISINES


# Agmarknet commodity names -> canonical (for seeding current prices)
AGMARKNET_MAP = {
    "Onion": "onion", "Tomato": "tomato", "Potato": "potato",
    "Brinjal": "brinjal", "Bhindi(Ladies Finger)": "okra",
    "Bitter gourd": "bitter_gourd", "Bottle gourd": "bottle_gourd",
    "Ridgeguard(Tori)": "ridge_gourd", "Pumpkin": "pumpkin",
    "Cabbage": "cabbage", "Cauliflower": "cauliflower",
    "Carrot": "carrot", "Beans": "beans", "Cluster beans": "beans",
    "Beetroot": "beetroot", "Raddish": "radish", "Drumstick": "drumstick",
    "Cucumbar(Kheera)": "cucumber", "Banana - Green": "banana", "Banana": "banana",
    "Amaranthus": "amaranthus", "Spinach": "spinach", "Methi(Leaves)": "fenugreek_leaves",
    "Green Chilli": "green_chilli", "Ginger(Green)": "ginger", "Garlic": "garlic",
    "Coriander(Leaves)": "coriander_leaves", "Mint(Pudina)": "mint",
    "Lemon": "lemon", "Coconut": "coconut", "Capsicum": "capsicum",
    "Green Peas": "green_peas", "Green Gram (Moong)(Whole)": "moong_dal",
    "Bengal Gram(Gram)(Whole)": "chana_dal", "Arhar (Tur/Red Gram)(Whole)": "toor_dal",
    "Black Gram (Urd Beans)(Whole)": "urad_dal", "Masur Dal": "masoor_dal",
    "Kabuli Chana(Chickpeas-White)": "chickpeas", "Groundnut": "groundnut",
    "Rice": "rice", "Paddy(Dhan)(Common)": "rice", "Wheat": "wheat_flour",
    "Potato(Sweet)": "potato", "Fish": "fish", "Egg": "egg",
}

# WFP commodity names -> canonical (for price history / GARCH training)
WFP_MAP = {
    "Rice": "rice", "Wheat": "wheat_flour", "Wheat flour": "wheat_flour",
    "Sugar": "sugar", "Sugar (jaggery/gur)": "jaggery",
    "Oil (mustard)": "mustard_oil", "Oil (sunflower)": "sunflower_oil",
    "Potatoes": "potato", "Onions": "onion", "Tomatoes": "tomato",
    "Salt (iodised)": "salt", "Milk (pasteurized)": "milk", "Milk": "milk",
    "Lentils (masur)": "masoor_dal", "Lentils": "toor_dal",
    "Lentils (moong)": "moong_dal", "Lentils (urad)": "urad_dal",
    "Chickpeas": "chickpeas", "Ghee (vanaspati)": "ghee",
    "Tea (black)": "tea", "Eggs": "egg", "Chickpea (flour)": "besan",
    "Semolina": "semolina", "Wheat flour (refined)": "maida",
    "Turmeric (powder)": "turmeric", "Cumin seeds (whole)": "cumin_seeds",
    "Coriander seeds (whole)": "coriander_powder", "Chili (red, dry raw)": "red_chilli_powder",
    "Ghee (desi)": "ghee", "Butter": "butter", "Bananas": "banana",
    "Eggplants": "brinjal", "Garlic": "garlic",
}


import re as _re


def canonical_for(text: str) -> str | None:
    """Longest alias contained in the lowercased ingredient text, or None.
    Punctuation is treated as whitespace so "Karela (Bitter Gourd/Pavakkai)"
    matches both "karela" and "bitter gourd"."""
    t = " " + _re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
    best: tuple[int, str] | None = None
    for alias, canon in ALIASES.items():
        if f" {alias} " in t or f" {alias}s " in t:
            if best is None or len(alias) > best[0]:
                best = (len(alias), canon)
    return best[1] if best else None


def info(commodity: str) -> dict:
    return COMMODITY_INFO.get(commodity, {"cls": "vegetable", "cluster": "mixed_veg"})


def item_class(commodity: str) -> str:
    return info(commodity).get("cls", "vegetable")


def density(commodity: str) -> float:
    return info(commodity).get("density", 1.0 if item_class(commodity) in ("dairy_fresh",) else DEFAULT_DENSITY)


def unit_weight(commodity: str) -> float:
    return info(commodity).get("unit_g", DEFAULT_UNIT_G)
