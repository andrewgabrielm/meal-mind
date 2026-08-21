"""Pydantic v2 wire contract. Routers speak only these shapes."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Storage = Literal["room", "fridge", "freezer"]
Diet = Literal["vegetarian", "vegan", "eggetarian", "non_vegetarian"]
Course = Literal["breakfast", "lunch", "dinner"]


# ---------- auth ----------

class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^\S+@\S+\.\S+$")
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=100)


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str


class TokenOut(BaseModel):
    token: str
    user: UserOut


# ---------- pantry ----------

class PantryItemIn(BaseModel):
    commodity: str
    quantity_g: float = Field(gt=0)
    storage: Storage = "room"
    purchased_on: date | None = None
    item_class: str | None = None   # inferred from commodity when omitted


class PantryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    commodity: str
    quantity_g: float
    item_class: str
    storage: Storage
    purchased_on: date


class SpoilageIn(BaseModel):
    """Outcome report: item either spoiled or was consumed while still good."""
    item_class: str
    storage: Storage = "room"
    lifetime_days: float = Field(gt=0)
    spoiled: bool


class DecayItemOut(BaseModel):
    pantry_item_id: int
    commodity: str
    item_class: str
    storage: Storage
    age_days: float
    survival: float                  # S(t): P(still good now)
    urgency: float                   # P(spoils in next 48h | survived to t)
    forced_include: bool
    alpha_days: float                # posterior room-shelf-life scale
    learned_from_observations: bool


class DecayAssessmentOut(BaseModel):
    horizon_days: float
    items: list[DecayItemOut]
    forced_commodities: list[str]


# ---------- preferences ----------

class PreferenceIn(BaseModel):
    diet: Diet = "vegetarian"
    region: str = ""
    max_cook_mins: int = Field(default=60, gt=0)
    dislikes: list[str] = []
    family_size: int = Field(default=4, gt=0)


class PreferenceOut(PreferenceIn):
    pass


# ---------- plan generation ----------

class PlanRequest(BaseModel):
    budget_rs: float | None = Field(default=None, gt=0)
    days: int = Field(default=3, ge=1, le=7)
    family_size: int | None = Field(default=None, gt=0)
    diet: Diet | None = None
    region: str | None = None
    max_cook_mins: int | None = None
    dislikes: list[str] | None = None
    # cap on meat/fish meals in this plan (None = no cap, 0 = veg-only week).
    # Applies to recipes whose diet is non_vegetarian; egg is governed by diet.
    max_nonveg_meals: int | None = Field(default=None, ge=0)


class MealIngredientOut(BaseModel):
    commodity: str
    grams: float                     # scaled to the plan's family size


class MealOut(BaseModel):
    day: int
    course: Course
    recipe_id: int
    recipe_name: str
    cluster: str
    diet: str = "vegetarian"         # the authoritative non-veg signal, not cluster
    time_mins: int
    locked: bool = False
    cooked: bool = False              # user marked it made; pantry already drawn
    ingredients: list[MealIngredientOut] = []
    instructions: str = ""
    source_url: str = ""


class PackOut(BaseModel):
    pack_size_g: float
    unit_price_rs: float
    count: int


class ShoppingItemOut(BaseModel):
    commodity: str
    required_g: float
    pantry_g: float
    bought_g: float
    surplus_g: float                 # what fixed pack sizes forced beyond need
    cost_rs: float
    packs: list[PackOut]
    price_source: str                # live | cache | seed
    optional_dropped: bool = False
    # advisory only — never enters the purchase ILP
    trend_pct: float | None = None       # forecast next-month price change %
    volatility_pct: float | None = None  # GARCH conditional volatility %
    advice: str | None = None            # buy_now | normal | wait_if_possible


class VarietyOut(BaseModel):
    entropy_bits: float
    max_entropy_bits: float
    normalised_entropy: float
    attainable_clusters: int
    penalties_engaged: bool
    distribution: dict[str, float]


class LeftoverMealOut(BaseModel):
    recipe_id: int
    recipe_name: str
    urgency_value: float
    uses: dict[str, float]           # commodity -> grams drawn


class TotalsOut(BaseModel):
    budget_rs: float
    spent_rs: float
    within_budget: bool
    surplus_value_rs: float
    # False when the basket cannot be bought at ANY price under this ceiling.
    # Without this an infeasible solve reports spent=0, which reads as "free".
    affordable: bool = True
    min_budget_rs: float | None = None   # cheapest basket that would cover it
    dropped: list[str] = []              # optional items sacrificed to fit


class PlanResponse(BaseModel):
    plan_id: int
    planner: Literal["llm", "heuristic"]
    planner_note: str = ""           # why the LLM was skipped, when it was
    days: int
    family_size: int
    meals: list[MealOut]
    shopping_list: list[ShoppingItemOut]
    totals: TotalsOut
    variety: VarietyOut
    decay: DecayAssessmentOut
    leftover_plan: list[LeftoverMealOut]


# ---------- UX revamp: pantry tick + swap ----------

class PantryTick(BaseModel):
    commodity: str
    # 0 means "I don't have this after all" — the pantry row is removed, so the
    # dialog can untick a mistake instead of only ever adding
    quantity_g: float = Field(ge=0)
    storage: Storage = "room"
    age_days: float = Field(default=0, ge=0)


class PlanPantryRequest(BaseModel):
    """Tick what you already have. Recipes are FROZEN — only stages 4-7 re-run."""
    ticks: list[PantryTick]


class SlotRef(BaseModel):
    day: int
    course: Course


class SwapRequest(BaseModel):
    """Refill unlocked slots with fresh recipes; locked slots are kept."""
    locked: list[SlotRef] = []


class CookedRequest(SlotRef):
    """Mark one meal as actually cooked. Its ingredients are drawn from the
    pantry, oldest stock first."""


class CookedOut(BaseModel):
    plan: PlanResponse
    used_from_pantry: dict[str, float]   # commodity -> grams deducted
    bought_not_stocked: dict[str, float]  # needed but never in the pantry
    items_emptied: int
    learned: int                          # censored observations recorded
