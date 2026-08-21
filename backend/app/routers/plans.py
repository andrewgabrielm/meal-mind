"""Plan endpoints. HTTP only: validate, delegate to services, persist via
repositories, return.

UX revamp contract:
- POST /plans/{id}/pantry — apply ticked pantry, re-solve stages 4-7 only.
  Recipes are FROZEN: a pantry tick must never change which meals are
  suggested.
- POST /plans/{id}/swap — refill unlocked slots with fresh recipes, re-solve
  stages 4-7. User-initiated swaps change recipes; the two paths stay
  separate.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import presenter, repositories as repo, schemas
from ..db import get_db
from ..services import consumption as consumption_svc
from ..services import planner as planner_svc
from ..services.planner import PlanOutcome
from .auth import current_user_id

router = APIRouter(prefix="/plans", tags=["plans"])


def _persist_and_respond(db: Session, uid: int, plan, outcome: PlanOutcome, eff: dict,
                         ticks: list[dict], cooked: list[str] | None = None) -> schemas.PlanResponse:
    names = {c.id: c.name for c in repo.candidate_recipes(db)}
    response = presenter.to_response(plan.id, outcome, eff, names, set(cooked or ()))
    repo.update_plan(db, plan,
                     state_json=presenter.meals_state(outcome.meals, ticks, cooked),
                     response_json=response.model_dump(mode="json"))
    repo.replace_meal_log(db, uid, plan.id, presenter.log_rows(outcome.meals))
    return response


@router.post("/generate", response_model=schemas.PlanResponse)
def generate(body: schemas.PlanRequest, db: Session = Depends(get_db),
             uid: int = Depends(current_user_id)):
    eff = presenter.effective_request(db, uid, body)
    inputs = presenter.build_inputs(db, uid, eff)
    try:
        outcome = planner_svc.build_plan(inputs, presenter.llm_from_settings())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if outcome.resolution.to_cache:
        repo.cache_live_packs(db, outcome.resolution.to_cache)

    plan = repo.create_plan(db, uid, request_json=eff,
                            state_json={}, response_json={})
    return _persist_and_respond(db, uid, plan, outcome, eff, ticks=[])


@router.get("/{plan_id}", response_model=schemas.PlanResponse)
def get_plan(plan_id: int, db: Session = Depends(get_db),
             uid: int = Depends(current_user_id)):
    plan = repo.get_plan(db, uid, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return schemas.PlanResponse(**json.loads(plan.response_json))


def _load(db: Session, uid: int, plan_id: int):
    plan = repo.get_plan(db, uid, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan, json.loads(plan.request_json), json.loads(plan.state_json)


@router.post("/{plan_id}/pantry", response_model=schemas.PlanResponse)
def apply_pantry(plan_id: int, body: schemas.PlanPantryRequest,
                 db: Session = Depends(get_db), uid: int = Depends(current_user_id)):
    """Tick what you already have; stages 4-7 re-solve. Recipes FROZEN."""
    plan, eff, state = _load(db, uid, plan_id)
    ticks = [t.model_dump() for t in body.ticks]
    # Ticking "I already have this" is a statement about the real kitchen, so
    # persist it: the item joins the pantry, starts ageing, and the decay model
    # begins tracking it. Upserted by commodity, so re-ticking is idempotent.
    today = date.today()
    for t in body.ticks:
        if t.quantity_g <= 0:            # unticked: I don't have this after all
            repo.remove_pantry_commodity(db, uid, t.commodity)
            continue
        repo.upsert_pantry_item(
            db, uid, commodity=t.commodity, quantity_g=t.quantity_g,
            storage=t.storage, purchased_on=today - timedelta(days=int(t.age_days)),
        )
    inputs = presenter.build_inputs(db, uid, eff)

    meals = presenter.meals_from_state(state, inputs.candidates)
    if not meals:
        raise HTTPException(status_code=409, detail="plan meals no longer resolvable")
    # recipes frozen: only steps 1 (for urgency) and 4-7 run again
    from ..services import decay as decay_svc
    assessments = decay_svc.assess_pantry(inputs.pantry_items, inputs.observations)
    req, resolution, purchase, leftover, forecasts, min_budget = planner_svc.solve_economics(
        meals, inputs, assessments)

    prev = json.loads(plan.response_json)
    outcome = PlanOutcome(
        planner_used=prev.get("planner", "heuristic"), meals=meals,
        assessments=assessments,
        variety_report=_variety_from(inputs, meals), purchase=purchase,
        resolution=resolution, requirements=req,
        pantry_totals=planner_svc.pantry_totals(inputs.pantry_items),
        leftover=leftover, forecasts=forecasts, min_budget_rs=min_budget,
    )
    if resolution.to_cache:
        repo.cache_live_packs(db, resolution.to_cache)
    return _persist_and_respond(db, uid, plan, outcome, eff, ticks, state.get("cooked"))


@router.post("/{plan_id}/swap", response_model=schemas.PlanResponse)
def swap(plan_id: int, body: schemas.SwapRequest, db: Session = Depends(get_db),
         uid: int = Depends(current_user_id)):
    """Refill unlocked slots; locked meals are kept. Re-solve stages 4-7."""
    plan, eff, state = _load(db, uid, plan_id)
    ticks = state.get("ticks", [])
    inputs = presenter.build_inputs(db, uid, eff)

    current = presenter.meals_from_state(state, inputs.candidates)
    locked_slots = {(s.day, s.course) for s in body.locked}
    locked = [m for m in current if (m.day, m.course) in locked_slots]
    swapped_away = {m.recipe.id for m in current if (m.day, m.course) not in locked_slots}
    try:
        meals, assessments, report = planner_svc.refill_slots(inputs, locked, swapped_away)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    req, resolution, purchase, leftover, forecasts, min_budget = planner_svc.solve_economics(
        meals, inputs, assessments)
    outcome = PlanOutcome(
        planner_used="heuristic", meals=meals, assessments=assessments,
        variety_report=report, purchase=purchase, resolution=resolution,
        requirements=req, pantry_totals=planner_svc.pantry_totals(inputs.pantry_items),
        leftover=leftover, forecasts=forecasts, min_budget_rs=min_budget,
    )
    if resolution.to_cache:
        repo.cache_live_packs(db, resolution.to_cache)
    # swapped-away meals were never cooked, so cooked marks do not carry over
    kept = {presenter.slot_key(m.day, m.course) for m in meals if m.locked}
    cooked = [k for k in state.get("cooked", []) if k in kept]
    return _persist_and_respond(db, uid, plan, outcome, eff, ticks, cooked)


@router.post("/{plan_id}/cooked", response_model=schemas.CookedOut)
def mark_cooked(plan_id: int, body: schemas.CookedRequest,
                db: Session = Depends(get_db), uid: int = Depends(current_user_id)):
    """Mark a meal as actually cooked: draw its ingredients from the pantry,
    oldest stock first, and re-solve the remaining shopping list."""
    plan, eff, state = _load(db, uid, plan_id)
    key = presenter.slot_key(body.day, body.course)
    cooked = list(state.get("cooked", []))
    if key in cooked:
        raise HTTPException(status_code=409, detail="that meal is already marked cooked")

    inputs = presenter.build_inputs(db, uid, eff)
    meals = presenter.meals_from_state(state, inputs.candidates)
    meal = next((m for m in meals if m.day == body.day and m.course == body.course), None)
    if meal is None:
        raise HTTPException(status_code=404, detail="no such meal in this plan")

    plan_c = consumption_svc.plan_consumption(
        inputs.pantry_items, meal.recipe.needs_for(eff["family_size"]))
    emptied = repo.apply_consumption(db, uid, plan_c.draws)

    # An item eaten before it spoiled is a right-censored observation — the
    # exact data the Weibull posterior wants. Cooking teaches feature (b)
    # without the user reporting anything.
    learned = 0
    for d in plan_c.exhausted_draws:
        if d.item_class and d.age_days > 0:
            repo.add_spoilage(db, uid, item_class=d.item_class, storage=d.storage,
                              lifetime_days=max(0.5, d.age_days), spoiled=False)
            learned += 1

    cooked.append(key)
    inputs = presenter.build_inputs(db, uid, eff)      # pantry has changed
    from ..services import decay as decay_svc
    assessments = decay_svc.assess_pantry(inputs.pantry_items, inputs.observations)
    req, resolution, purchase, leftover, forecasts, min_budget = planner_svc.solve_economics(
        meals, inputs, assessments)
    prev = json.loads(plan.response_json)
    outcome = PlanOutcome(
        planner_used=prev.get("planner", "heuristic"), meals=meals,
        assessments=assessments, variety_report=_variety_from(inputs, meals),
        purchase=purchase, resolution=resolution, requirements=req,
        pantry_totals=planner_svc.pantry_totals(inputs.pantry_items),
        leftover=leftover, forecasts=forecasts, min_budget_rs=min_budget,
    )
    response = _persist_and_respond(db, uid, plan, outcome, eff,
                                    state.get("ticks", []), cooked)
    return schemas.CookedOut(
        plan=response, used_from_pantry=plan_c.taken,
        bought_not_stocked=plan_c.short, items_emptied=emptied, learned=learned,
    )


def _variety_from(inputs, meals):
    from ..services import variety as variety_svc
    pool = planner_svc.filter_candidates(inputs)
    return variety_svc.measure(inputs.history_clusters,
                               {r.cluster for r in pool}, inputs.diet)

