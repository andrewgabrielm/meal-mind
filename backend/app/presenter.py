"""Wiring between HTTP and the service layer: assemble PlanInputs from
repository data, and serialize PlanOutcome into the Pydantic wire contract.
No business logic — construction and formatting only."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from . import repositories as repo
from . import schemas
from .config import get_settings
from .services import decay, variety
from .services.planner import CandidateRecipe, Meal, PlanInputs, PlanOutcome, LLMPlanner
from .services.quickcommerce import QuickCommerceClient


def llm_from_settings() -> LLMPlanner | None:
    s = get_settings()
    if not s.llm_api_key:
        return None
    return LLMPlanner(
        base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model,
        fallback_models=[m.strip() for m in s.llm_fallback_models.split(",") if m.strip()],
    )


def live_client_from_settings() -> QuickCommerceClient | None:
    s = get_settings()
    if not s.quickcommerce_api_key:
        return None
    return QuickCommerceClient(base_url=s.quickcommerce_base_url, api_key=s.quickcommerce_api_key)


def effective_request(db: Session, user_id: int, req: schemas.PlanRequest) -> dict:
    """Request overrides win; preferences fill the gaps; settings backstop."""
    s = get_settings()
    pref = repo.get_preferences(db, user_id)
    return {
        "budget_rs": req.budget_rs if req.budget_rs is not None else s.default_budget_rs,
        "days": req.days,
        "family_size": req.family_size if req.family_size is not None else pref.family_size,
        "diet": req.diet if req.diet is not None else pref.diet,
        "region": req.region if req.region is not None else pref.region,
        "max_cook_mins": req.max_cook_mins if req.max_cook_mins is not None else pref.max_cook_mins,
        "dislikes": req.dislikes if req.dislikes is not None else json.loads(pref.dislikes_json),
        "max_nonveg_meals": req.max_nonveg_meals,
    }


def build_inputs(db: Session, user_id: int, eff: dict) -> PlanInputs:
    pantry = repo.pantry_as_dicts(db, user_id)
    return PlanInputs(
        budget_rs=eff["budget_rs"], days=eff["days"], family_size=eff["family_size"],
        diet=eff["diet"], region=eff["region"], max_cook_mins=eff["max_cook_mins"],
        dislikes=eff["dislikes"],
        pantry_items=pantry,
        observations=repo.observations_for(db, user_id),
        candidates=repo.candidate_recipes(db),
        history_clusters=repo.recent_meal_clusters(db, user_id, limit=variety.WINDOW_DAYS * variety.MEALS_PER_DAY),
        cached_packs=repo.packs_by_source(db, "live"),
        seeded_packs=repo.packs_by_source(db, "seed"),
        live_client=live_client_from_settings(),
        price_history=repo.price_history(db),
        max_nonveg_meals=eff.get("max_nonveg_meals"),
    )


def meals_state(meals: list[Meal], ticks: list[dict] | None = None,
                cooked: list[str] | None = None) -> dict:
    return {
        "meals": [
            {"day": m.day, "course": m.course, "recipe_id": m.recipe.id, "locked": m.locked}
            for m in meals
        ],
        "ticks": ticks or [],
        "cooked": sorted(set(cooked or [])),   # "day|course" slot keys
    }


def slot_key(day: int, course: str) -> str:
    return f"{day}|{course}"


def meals_from_state(state: dict, candidates: list[CandidateRecipe]) -> list[Meal]:
    by_id = {r.id: r for r in candidates}
    meals = []
    for m in state["meals"]:
        r = by_id.get(m["recipe_id"])
        if r is None:                      # recipe removed from corpus since
            continue
        meals.append(Meal(day=m["day"], course=m["course"], recipe=r,
                          locked=m.get("locked", False)))
    return meals


def log_rows(meals: list[Meal]) -> list[dict]:
    return [
        {"recipe_id": m.recipe.id, "day": m.day, "course": m.course, "cluster": m.recipe.cluster}
        for m in meals
    ]


def decay_out(assessments: list[decay.Assessment], horizon_days: float = 2.0) -> schemas.DecayAssessmentOut:
    return schemas.DecayAssessmentOut(
        horizon_days=horizon_days,
        items=[
            schemas.DecayItemOut(
                pantry_item_id=a.pantry_item_id, commodity=a.commodity,
                item_class=a.item_class, storage=a.storage,
                age_days=round(a.age_days, 2), survival=round(a.survival, 4),
                urgency=round(a.urgency, 4), forced_include=a.forced_include,
                alpha_days=round(a.alpha_days, 2),
                learned_from_observations=a.learned_from_observations,
            )
            for a in assessments
        ],
        forced_commodities=decay.forced_commodities(assessments),
    )


def to_response(plan_id: int, outcome: PlanOutcome, eff: dict,
                recipe_names: dict[int, str] | None = None,
                cooked_slots: set[str] | None = None) -> schemas.PlanResponse:
    names = recipe_names or {}
    v = outcome.variety_report
    surplus_value = 0.0
    shopping = []
    for ln in outcome.purchase.lines:
        f = outcome.forecasts.get(ln.commodity)
        if ln.bought_g > 0:
            surplus_value += ln.cost_rs * (ln.surplus_g / ln.bought_g)
        shopping.append(schemas.ShoppingItemOut(
            commodity=ln.commodity,
            required_g=round(ln.required_g, 1), pantry_g=round(ln.pantry_g, 1),
            bought_g=round(ln.bought_g, 1), surplus_g=round(ln.surplus_g, 1),
            cost_rs=round(ln.cost_rs, 2),
            packs=[schemas.PackOut(pack_size_g=p.size_g, unit_price_rs=p.price_rs, count=n)
                   for p, n in ln.packs],
            price_source=outcome.resolution.source_of(ln.commodity),
            optional_dropped=ln.optional_dropped,
            trend_pct=f.trend_pct if f else None,
            volatility_pct=f.volatility_pct if f else None,
            advice=f.advice if f else None,
        ))

    return schemas.PlanResponse(
        plan_id=plan_id,
        planner=outcome.planner_used, planner_note=outcome.planner_note,
        days=eff["days"], family_size=eff["family_size"],
        meals=[
            schemas.MealOut(
                day=m.day, course=m.course, recipe_id=m.recipe.id,
                recipe_name=m.recipe.name, cluster=m.recipe.cluster,
                diet=m.recipe.diet, time_mins=m.recipe.time_mins, locked=m.locked,
                cooked=slot_key(m.day, m.course) in (cooked_slots or ()),
                ingredients=[
                    schemas.MealIngredientOut(commodity=c, grams=round(g, 1))
                    for c, g in sorted(m.recipe.needs_for(eff["family_size"]).items(),
                                       key=lambda kv: -kv[1])
                ],
                instructions=m.recipe.instructions,
                source_url=m.recipe.source_url,
            )
            for m in outcome.meals
        ],
        shopping_list=shopping,
        totals=schemas.TotalsOut(
            budget_rs=eff["budget_rs"],
            spent_rs=round(outcome.purchase.total_cost_rs, 2),
            # an infeasible solve buys nothing and costs nothing — that is NOT
            # "within budget", it means the basket is unaffordable
            within_budget=(outcome.purchase.feasible
                           and outcome.purchase.total_cost_rs <= eff["budget_rs"] + 1e-6),
            surplus_value_rs=round(surplus_value, 2),
            affordable=outcome.purchase.feasible,
            min_budget_rs=outcome.min_budget_rs,
            dropped=list(outcome.purchase.dropped),
        ),
        variety=schemas.VarietyOut(
            entropy_bits=v.entropy_bits, max_entropy_bits=v.max_entropy_bits,
            normalised_entropy=v.normalised_entropy,
            attainable_clusters=v.attainable_clusters,
            penalties_engaged=v.penalties_engaged, distribution=v.distribution,
        ),
        decay=decay_out(outcome.assessments),
        leftover_plan=[
            schemas.LeftoverMealOut(
                recipe_id=p.recipe_id,
                recipe_name=names.get(p.recipe_id, f"recipe {p.recipe_id}"),
                urgency_value=round(p.value, 3),
                uses={c: round(g, 1) for c, g in p.uses.items()},
            )
            for p in outcome.leftover
        ],
    )
