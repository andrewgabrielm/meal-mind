"""Retrieval, LLM arrangement, and pipeline assembly.

POST /api/v1/plans/generate runs, in this order (deliberate):

  1  decay assessment    pantry ages -> urgency weights + forced-include list
  2  recipe retrieval    diet filter, pantry-overlap ranking, variety penalty
  3  LLM arrangement     picks recipe_ids into day/course slots — SEES NO PRICES
  4  requirements        chosen recipes -> grams per commodity x family_size
  5  price resolution    QuickCommerce -> cache -> seed
  6  purchase ILP        min-cost packs under hard budget -> list + surplus
  7  leftover ILP        surplus + stock -> 48h meals, weighted by step-1 urgency
  8  log + respond

The LLM sits in the MIDDLE, not the end: if it were last it would see prices
and decide affordability, which no LLM can be trusted to do. Everything after
step 3 is deterministic math. The LLM may emit only recipe_ids it was handed;
every id is validated and a plan containing an unknown one is rejected and
re-prompted once, then the deterministic HeuristicPlanner takes over.

Services never touch SQL: the router feeds this module plain data via
PlanInputs and persists what comes back.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

from . import decay, forecast, variety
from .optimizer import LeftoverPick, LeftoverRecipe, Pack, PurchaseResult, solve_leftover, solve_purchase
from .pricing import PriceResolution, resolve_prices
from .quickcommerce import QuickCommerceClient
from scripts.ingredient_tables import OPTIONAL_COMMODITIES

COURSES = ["breakfast", "lunch", "dinner"]
FORCED_INCLUDE_BONUS = 10.0     # feature (b)'s trump card — variety is capped at 4.0
OVERLAP_WEIGHT = 3.0
URGENCY_WEIGHT = 2.0
CANDIDATE_POOL = 24             # recipes handed to the arranger
LEFTOVER_SLOTS = 4              # meals in the 48-hour utilisation plan

# recipe diets acceptable for a household diet
DIET_ALLOWS = {
    "vegan": {"vegan"},
    "vegetarian": {"vegan", "vegetarian"},
    "eggetarian": {"vegan", "vegetarian", "eggetarian"},
    "non_vegetarian": {"vegan", "vegetarian", "eggetarian", "non_vegetarian"},
}


@dataclass
class CandidateRecipe:
    id: int
    name: str
    course: str                  # breakfast | lunch | dinner | side | snack
    diet: str
    region: str
    time_mins: int
    servings: int
    ingredients: list[tuple[str, float]]     # (commodity, grams at recipe.servings)
    instructions: str = ""
    source_url: str = ""
    cluster: str = ""
    score: float = 0.0

    def __post_init__(self):
        if not self.cluster:
            self.cluster = variety.assign_cluster(self.ingredients)

    def needs_for(self, family_size: int) -> dict[str, float]:
        scale = family_size / max(self.servings, 1)
        needs: dict[str, float] = {}
        for c, g in self.ingredients:
            needs[c] = needs.get(c, 0.0) + g * scale
        return needs

    def fits_course(self, course: str) -> bool:
        if self.course == course:
            return True
        return course in ("lunch", "dinner") and self.course in ("lunch", "dinner")


@dataclass
class Meal:
    day: int
    course: str
    recipe: CandidateRecipe
    locked: bool = False


@dataclass
class PlanInputs:
    budget_rs: float
    days: int
    family_size: int
    diet: str
    region: str
    max_cook_mins: int
    dislikes: list[str]
    pantry_items: list[dict]                 # id, commodity, item_class, storage, age_days, quantity_g
    observations: list[decay.Observation]
    candidates: list[CandidateRecipe]        # full corpus, unfiltered
    history_clusters: list[str]              # recent meal_plan_log clusters, oldest first
    cached_packs: dict[str, list[Pack]]
    seeded_packs: dict[str, list[Pack]]
    live_client: QuickCommerceClient | None = None
    price_history: dict[str, list[float]] = field(default_factory=dict)
    # cap on meat/fish meals (recipes with diet=non_vegetarian) in the plan.
    # None = unlimited, 0 = veg-only week. Egg is governed by the diet field.
    max_nonveg_meals: int | None = None


@dataclass
class PlanOutcome:
    planner_used: str
    meals: list[Meal]
    assessments: list[decay.Assessment]
    variety_report: variety.VarietyReport
    purchase: PurchaseResult
    resolution: PriceResolution
    requirements: dict[str, float]
    pantry_totals: dict[str, float]
    leftover: list[LeftoverPick]
    forecasts: dict[str, forecast.Forecast]
    planner_note: str = ""        # why the LLM was not used, when it wasn't
    min_budget_rs: float | None = None   # cheapest basket covering the plan


# ------------------------------------------------------------ steps 1 and 2

def pantry_totals(pantry_items: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for it in pantry_items:
        totals[it["commodity"]] = totals.get(it["commodity"], 0.0) + it["quantity_g"]
    return totals


def filter_candidates(inputs: PlanInputs) -> list[CandidateRecipe]:
    allowed = DIET_ALLOWS.get(inputs.diet, DIET_ALLOWS["vegetarian"])
    dislikes = set(inputs.dislikes)
    out = []
    for r in inputs.candidates:
        if r.diet not in allowed:
            continue
        if inputs.region and r.region and r.region != inputs.region:
            continue
        if r.time_mins > inputs.max_cook_mins:
            continue
        if dislikes and any(c in dislikes for c, _ in r.ingredients):
            continue
        out.append(r)
    return out


def rank_candidates(
    pool: list[CandidateRecipe],
    pantry: dict[str, float],
    urgency: dict[str, float],
    forced: list[str],
    report: variety.VarietyReport,
) -> list[CandidateRecipe]:
    """Pantry-overlap ranking + urgency bonus - variety penalty. The
    forced-include bonus (+10) always beats the entropy penalty (<= 4)."""
    forced_set = set(forced)
    for r in pool:
        total_g = sum(g for _, g in r.ingredients) or 1.0
        overlap_g = sum(min(g, pantry.get(c, 0.0)) for c, g in r.ingredients)
        score = OVERLAP_WEIGHT * (overlap_g / total_g)
        score += URGENCY_WEIGHT * sum(
            urgency.get(c, 0.0) * (g / total_g) for c, g in r.ingredients
        )
        if any(c in forced_set for c, _ in r.ingredients):
            score += FORCED_INCLUDE_BONUS
        score -= variety.penalty_for(report, r.cluster)
        r.score = round(score, 4)
    return sorted(pool, key=lambda r: -r.score)


def clip_pool(ranked: list[CandidateRecipe], max_nonveg: int | None) -> list[CandidateRecipe]:
    """Top-N candidate pool for the arranger — but when the household asked
    for N non-veg meals, low-ranking meat recipes must not fall off the list,
    or the wish can never be satisfied. Extras are drawn cluster-diverse
    (chicken, fish, mutton round-robin), or a same-day pair of non-veg meals
    would always collide with the one-family-per-day rule."""
    pool = ranked[:CANDIDATE_POOL]
    if not max_nonveg:
        return pool
    nonveg_counts: dict[str, int] = {}
    for r in pool:
        if is_nonveg(r):
            nonveg_counts[r.cluster] = nonveg_counts.get(r.cluster, 0) + 1
    short = max_nonveg - sum(nonveg_counts.values())
    if short <= 0:
        return pool
    by_cluster: dict[str, list[CandidateRecipe]] = {}
    for r in ranked[CANDIDATE_POOL:]:
        if is_nonveg(r):
            by_cluster.setdefault(r.cluster, []).append(r)
    extras: list[CandidateRecipe] = []
    while len(extras) < short and by_cluster:
        # always feed the least-represented family next
        cluster = min(by_cluster, key=lambda c: nonveg_counts.get(c, 0))
        extras.append(by_cluster[cluster].pop(0))
        nonveg_counts[cluster] = nonveg_counts.get(cluster, 0) + 1
        if not by_cluster[cluster]:
            del by_cluster[cluster]
    return pool + extras


# ---------------------------------------------------------------- arrangers

def is_nonveg(recipe: CandidateRecipe) -> bool:
    """Meat/fish counts against the non-veg cap; egg is a diet-level choice."""
    return recipe.diet == "non_vegetarian"


class HeuristicPlanner:
    """Deterministic fallback: greedy fill, best score first, avoiding
    same-cluster meals on the same day when an alternative exists. The
    non-veg cap is a hard rule at every relaxation level."""

    name = "heuristic"

    def arrange(self, ranked: list[CandidateRecipe], days: int,
                max_nonveg: int | None = None, nonveg_used: int = 0) -> list[Meal] | None:
        used: set[int] = set()
        meals: list[Meal] = []
        # spread the wish across days: cap 2 over 3 days = 1 meat meal/day max,
        # cap 6 over 3 days = lunch AND dinner may be non-veg
        day_quota = -(-max_nonveg // days) if max_nonveg else 0
        for day in range(1, days + 1):
            day_clusters: set[str] = set()
            nonveg_today = 0
            for course in COURSES:
                allow_nv = max_nonveg is None or nonveg_used < max_nonveg
                pick = None
                # a cap of N is also a WISH for N: "non-veg twice a week" means
                # two meat meals, so spend the allowance at lunch/dinner
                if max_nonveg and allow_nv and course != "breakfast" and nonveg_today < day_quota:
                    pick = self._pick(ranked, used, course, day_clusters, True, only_nonveg=True)
                if pick is None:
                    pick = self._pick(ranked, used, course, day_clusters, allow_nv)
                if pick is None:
                    pick = self._pick(ranked, used, course, set(), allow_nv)   # relax cluster rule
                if pick is None:
                    pick = self._pick(ranked, set(), course, set(), allow_nv)  # allow repeats
                if pick is None:
                    return None
                used.add(pick.id)
                day_clusters.add(pick.cluster)
                if is_nonveg(pick):
                    nonveg_used += 1
                    nonveg_today += 1
                meals.append(Meal(day=day, course=course, recipe=pick))
        return meals

    @staticmethod
    def _pick(ranked, used, course, avoid_clusters, allow_nonveg=True, only_nonveg=False):
        for r in ranked:
            if r.id in used or not r.fits_course(course):
                continue
            if r.cluster in avoid_clusters:
                continue
            if not allow_nonveg and is_nonveg(r):
                continue
            if only_nonveg and not is_nonveg(r):
                continue
            return r
        return None


class LLMPlanner:
    """Arranges retrieved recipes into day/course slots via an OpenAI-
    compatible chat-completions endpoint (Grok or OpenAI). It NEVER sees
    prices, never sums a basket, never decides affordability. Grounding:
    only handed recipe_ids are valid; one re-prompt, then heuristic."""

    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0,
                 fallback_models: list[str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # a quota is per MODEL per day, so a different model is a fresh budget
        self.models = [model] + [m for m in (fallback_models or []) if m and m != model]
        self.model_used: str | None = None
        self.last_error: str | None = None   # why the heuristic took over

    # free tiers throttle and overload constantly; one 503 should not silently
    # demote the whole plan to the heuristic
    RETRY_STATUSES = (429, 500, 502, 503, 504)
    MAX_ATTEMPTS = 3

    # a daily quota or a missing model is permanent for this run: change model
    SWITCH_STATUSES = (429, 404)

    def _chat(self, messages: list[dict]) -> str:
        last = ""
        for model in self.models:
            for attempt in range(self.MAX_ATTEMPTS):
                try:
                    resp = httpx.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": model, "messages": messages, "temperature": 0.4},
                        timeout=self.timeout,
                    )
                except Exception as e:                   # network/timeout
                    last = f"{type(e).__name__}: {e}"
                    self.last_error = last
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code == 200:
                    self.model_used = model
                    self.last_error = None
                    return resp.json()["choices"][0]["message"]["content"]
                if resp.status_code in self.SWITCH_STATUSES:
                    # out of daily quota (or no such model) — try the next one
                    last = (f"{model}: daily free-tier quota exhausted"
                            if resp.status_code == 429 else f"{model}: no such model")
                    self.last_error = last
                    break
                if resp.status_code in self.RETRY_STATUSES and attempt < self.MAX_ATTEMPTS - 1:
                    last = f"{model}: HTTP {resp.status_code} from the provider"
                    self.last_error = last
                    time.sleep(1.5 * (attempt + 1))      # transient: back off
                    continue
                last = f"{model}: HTTP {resp.status_code}: {resp.text[:120]}"
                self.last_error = last
                break
        raise RuntimeError(last or "the model provider did not respond")

    def _prompt(self, ranked: list[CandidateRecipe], days: int,
                max_nonveg: int | None = None) -> str:
        lines = [
            f"id={r.id} | {r.name} | course={r.course} | family={r.cluster} | {r.time_mins} min"
            + (" | NON-VEG" if is_nonveg(r) else "")
            for r in ranked
        ]
        cap = ""
        if max_nonveg is not None:
            cap = (f" Include about {max_nonveg} and NEVER MORE THAN {max_nonveg} recipes "
                   "marked NON-VEG (at lunch or dinner); every other slot must use "
                   "unmarked (vegetarian) recipes.")
        return (
            "You arrange Indian home meals into a plan. Use ONLY the recipe ids listed "
            "below — never invent an id. Fill every slot: days 1.."
            f"{days}, courses breakfast, lunch and dinner each day. Prefer higher-listed "
            "recipes (they better match the pantry), vary the dish families across the "
            "plan, and put breakfast-course recipes at breakfast. Avoid repeating a "
            f"recipe unless unavoidable.{cap}\n\nRECIPES:\n" + "\n".join(lines) +
            '\n\nAnswer with ONLY this JSON, no prose:\n'
            '{"meals": [{"day": 1, "course": "breakfast", "recipe_id": 0}, ...]}'
        )

    def _validate(self, text: str, ranked: list[CandidateRecipe], days: int,
                  max_nonveg: int | None = None) -> list[Meal] | None:
        by_id = {r.id: r for r in ranked}
        try:
            start, end = text.index("{"), text.rindex("}") + 1
            data = json.loads(text[start:end])
            raw = data["meals"]
        except (ValueError, KeyError, TypeError):
            return None
        slots = {(d, c) for d in range(1, days + 1) for c in COURSES}
        meals: list[Meal] = []
        for m in raw:
            try:
                day, course, rid = int(m["day"]), str(m["course"]), int(m["recipe_id"])
            except (KeyError, TypeError, ValueError):
                return None
            if (day, course) not in slots or rid not in by_id:
                return None        # unknown id or bad slot: reject the whole plan
            slots.discard((day, course))
            meals.append(Meal(day=day, course=course, recipe=by_id[rid]))
        if slots:                  # unfilled slots: reject
            return None
        if max_nonveg is not None and sum(is_nonveg(m.recipe) for m in meals) > max_nonveg:
            return None            # over the non-veg cap: reject the whole plan
        return meals

    def arrange(self, ranked: list[CandidateRecipe], days: int,
                max_nonveg: int | None = None) -> list[Meal] | None:
        messages = [{"role": "user", "content": self._prompt(ranked, days, max_nonveg)}]
        for attempt in range(2):   # one re-prompt on a grounding failure
            try:
                text = self._chat(messages)
            except Exception as e:
                self.last_error = self.last_error or str(e)
                return None
            meals = self._validate(text, ranked, days, max_nonveg)
            if meals is not None:
                meals.sort(key=lambda m: (m.day, COURSES.index(m.course)))
                return meals
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content":
                    "Invalid: you used a recipe_id not in the list, missed a slot, or "
                    "exceeded the NON-VEG limit. Emit the JSON again using only the "
                    "listed ids, all slots filled, within the non-veg limit."},
            ]
        return None


# ------------------------------------------------------------ steps 4 to 7

def requirements_for(meals: list[Meal], family_size: int) -> dict[str, float]:
    """Step 4: chosen recipes -> grams per commodity, scaled by family size."""
    req: dict[str, float] = {}
    for m in meals:
        for c, g in m.recipe.needs_for(family_size).items():
            req[c] = req.get(c, 0.0) + g
    return {c: round(g, 1) for c, g in req.items()}


def solve_economics(
    meals: list[Meal],
    inputs: PlanInputs,
    assessments: list[decay.Assessment],
) -> tuple[dict[str, float], PriceResolution, PurchaseResult, list[LeftoverPick], dict[str, forecast.Forecast]]:
    """Steps 4-7. Re-run by /plans/{id}/pantry and /plans/{id}/swap; the meal
    list passed in is whatever those endpoints froze or refilled."""
    pantry = pantry_totals(inputs.pantry_items)
    req = requirements_for(meals, inputs.family_size)                     # 4

    resolution = resolve_prices(                                          # 5
        sorted(req), inputs.cached_packs, inputs.seeded_packs, inputs.live_client
    )
    packs = resolution.packs_dict()
    purchase = solve_purchase(                                            # 6
        req, pantry, packs, inputs.budget_rs, optional=OPTIONAL_COMMODITIES,
    )
    # If the ceiling makes the basket impossible, solve again with no ceiling
    # so we can tell the user what it would actually take. The guarded ILP is
    # reused as-is — nothing about the algorithm changes.
    min_budget: float | None = None
    if not purchase.feasible:
        # PuLP rejects an infinite constraint bound, so use a ceiling that
        # cannot bind: every commodity bought at its priciest pack, twice over.
        no_ceiling = 2.0 * sum(
            max(pk.price_rs for pk in options) * (1 + req.get(c, 0.0) / min(
                (pk.size_g for pk in options), default=1.0))
            for c, options in packs.items() if options
        ) + 1000.0
        relaxed = solve_purchase(req, pantry, packs, no_ceiling,
                                 optional=OPTIONAL_COMMODITIES)
        if relaxed.feasible:
            min_budget = round(relaxed.total_cost_rs, 2)

    bought = {ln.commodity: ln.bought_g for ln in purchase.lines}
    available = {                                                         # 7
        c: pantry.get(c, 0.0) + bought.get(c, 0.0)
        for c in set(pantry) | set(bought)
    }
    urgency = decay.urgency_weights(assessments)   # THE feature (a)<-(b) coupling
    chosen_ids = {m.recipe.id for m in meals}
    leftover_pool = [
        LeftoverRecipe(recipe_id=r.id, needs_g=r.needs_for(inputs.family_size))
        for r in filter_candidates(inputs)
        if r.id not in chosen_ids
    ]
    leftover = solve_leftover(leftover_pool, available, urgency, slots=LEFTOVER_SLOTS)

    forecasts = {
        c: f for c in req
        if (f := forecast.forecast_commodity(c, inputs.price_history.get(c, [])))
    }
    return req, resolution, purchase, leftover, forecasts, min_budget


# ---------------------------------------------------------------- pipeline

def choose_meals(inputs: PlanInputs, llm: LLMPlanner | None) \
        -> tuple[list[Meal], str, list[decay.Assessment], variety.VarietyReport,
                 list[CandidateRecipe], str]:
    """Steps 1-3. Last element is why the LLM was skipped, if it was."""
    assessments = decay.assess_pantry(inputs.pantry_items, inputs.observations)   # 1
    urgency = decay.urgency_weights(assessments)
    forced = decay.forced_commodities(assessments)

    pool = filter_candidates(inputs)                                              # 2
    report = variety.measure(inputs.history_clusters, {r.cluster for r in pool}, inputs.diet)
    ranked = clip_pool(rank_candidates(pool, pantry_totals(inputs.pantry_items),
                                       urgency, forced, report), inputs.max_nonveg_meals)

    meals, used, note = None, "heuristic", ""                                     # 3
    if llm is not None:
        meals = llm.arrange(ranked, inputs.days, inputs.max_nonveg_meals)
        if meals is not None:
            used = "llm"
        else:
            note = llm.last_error or "the model did not return a usable plan"
    else:
        note = "no LLM_API_KEY configured"
    if meals is None:
        meals = HeuristicPlanner().arrange(ranked, inputs.days, inputs.max_nonveg_meals)
    if meals is None:
        raise ValueError("no candidate recipes fit the requested filters")
    return meals, used, assessments, report, ranked, note


def refill_slots(inputs: PlanInputs, locked: list[Meal],
                 exclude_ids: set[int] = frozenset()) \
        -> tuple[list[Meal], list[decay.Assessment], variety.VarietyReport]:
    """UX revamp swap: keep locked meals, refill every other slot with fresh
    recipes. exclude_ids are the recipes being swapped AWAY — they must not
    come straight back (unless the corpus leaves no other choice). User-
    initiated swaps CHANGE recipes; pantry ticks never do — the two code
    paths stay separate by design."""
    assessments = decay.assess_pantry(inputs.pantry_items, inputs.observations)
    urgency = decay.urgency_weights(assessments)
    forced = decay.forced_commodities(assessments)
    pool = filter_candidates(inputs)
    report = variety.measure(inputs.history_clusters, {r.cluster for r in pool}, inputs.diet)
    ranked = clip_pool(rank_candidates(pool, pantry_totals(inputs.pantry_items),
                                       urgency, forced, report), inputs.max_nonveg_meals)

    locked_by_slot = {(m.day, m.course): m for m in locked}
    used = {m.recipe.id for m in locked} | set(exclude_ids)
    nonveg_used = sum(is_nonveg(m.recipe) for m in locked)   # locked meals count
    meals: list[Meal] = []
    h = HeuristicPlanner()
    for day in range(1, inputs.days + 1):
        day_clusters = {m.recipe.cluster for m in locked
                        if m.day == day}
        for course in COURSES:
            kept = locked_by_slot.get((day, course))
            if kept is not None:
                kept.locked = True
                meals.append(kept)
                continue
            allow_nv = inputs.max_nonveg_meals is None or nonveg_used < inputs.max_nonveg_meals
            pick = h._pick(ranked, used, course, day_clusters, allow_nv) \
                or h._pick(ranked, used, course, set(), allow_nv) \
                or h._pick(ranked, set(), course, set(), allow_nv)
            if pick is None:
                raise ValueError("no candidate recipes fit the requested filters")
            used.add(pick.id)
            day_clusters.add(pick.cluster)
            if is_nonveg(pick):
                nonveg_used += 1
            meals.append(Meal(day=day, course=course, recipe=pick))
    return meals, assessments, report


def build_plan(inputs: PlanInputs, llm: LLMPlanner | None = None) -> PlanOutcome:
    """The whole pipeline, steps 1-7. Step 8 (log + respond) is the router's."""
    meals, used, assessments, report, _, note = choose_meals(inputs, llm)
    req, resolution, purchase, leftover, forecasts, min_budget = solve_economics(meals, inputs, assessments)
    return PlanOutcome(
        planner_used=used, planner_note=note, min_budget_rs=min_budget,
        meals=meals, assessments=assessments,
        variety_report=report, purchase=purchase, resolution=resolution,
        requirements=req, pantry_totals=pantry_totals(inputs.pantry_items),
        leftover=leftover, forecasts=forecasts,
    )
