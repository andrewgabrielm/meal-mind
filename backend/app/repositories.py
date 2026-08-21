"""ALL database access lives here. No business logic — fetch, persist, return.
Services stay SQL-free; routers call these and delegate to services."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import models
from .services.decay import Observation
from .services.optimizer import Pack
from .services.planner import CandidateRecipe
from .services.quickcommerce import LivePack
from scripts.ingredient_tables import item_class as _item_class


# ---------------------------------------------------------------- users

def create_user(db: Session, *, email: str, name: str, password_hash: str) -> models.User:
    user = models.User(email=email.lower().strip(), name=name, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def user_by_email(db: Session, email: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.email == email.lower().strip()))


def get_user(db: Session, user_id: int) -> models.User | None:
    return db.get(models.User, user_id)


# ---------------------------------------------------------------- pantry

def list_pantry(db: Session, user_id: int) -> list[models.PantryItem]:
    return list(db.scalars(
        select(models.PantryItem).where(models.PantryItem.user_id == user_id)
        .order_by(models.PantryItem.purchased_on)
    ))


def pantry_as_dicts(db: Session, user_id: int, today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [
        {
            "id": p.id, "commodity": p.commodity, "item_class": p.item_class,
            "storage": p.storage, "quantity_g": p.quantity_g,
            "age_days": max(0.0, (today - p.purchased_on).days),
        }
        for p in list_pantry(db, user_id)
    ]


def add_pantry_item(db: Session, user_id: int, *, commodity: str, quantity_g: float,
                    storage: str, purchased_on: date, item_class: str | None) -> models.PantryItem:
    item = models.PantryItem(
        user_id=user_id, commodity=commodity, quantity_g=quantity_g,
        storage=storage, purchased_on=purchased_on,
        item_class=item_class or _item_class(commodity),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def upsert_pantry_item(db: Session, user_id: int, *, commodity: str, quantity_g: float,
                       storage: str, purchased_on: date) -> models.PantryItem:
    """Declare stock on hand. Ticking "I already have this" is a statement of
    how much is there, not an addition, so an existing row for the commodity is
    updated rather than duplicated — re-applying the same ticks is idempotent."""
    existing = db.scalar(
        select(models.PantryItem)
        .where(models.PantryItem.user_id == user_id,
               models.PantryItem.commodity == commodity)
        .order_by(models.PantryItem.purchased_on)
    )
    if existing is not None:
        existing.quantity_g = quantity_g
        existing.storage = storage
        existing.purchased_on = purchased_on
        db.commit()
        db.refresh(existing)
        return existing
    return add_pantry_item(db, user_id, commodity=commodity, quantity_g=quantity_g,
                           storage=storage, purchased_on=purchased_on, item_class=None)


def remove_pantry_commodity(db: Session, user_id: int, commodity: str) -> int:
    """Drop every row of one commodity — used when a tick is cleared."""
    rows = list(db.scalars(select(models.PantryItem).where(
        models.PantryItem.user_id == user_id,
        models.PantryItem.commodity == commodity)))
    for r in rows:
        db.delete(r)
    db.commit()
    return len(rows)


def apply_consumption(db: Session, user_id: int, draws) -> int:
    """Write back a services.consumption plan: reduce each drawn item, delete
    the ones emptied. Returns the number of rows removed."""
    removed = 0
    for d in draws:
        item = db.get(models.PantryItem, d.pantry_item_id)
        if item is None or item.user_id != user_id:
            continue
        if d.exhausted:
            db.delete(item)
            removed += 1
        else:
            item.quantity_g = d.grams_left
    db.commit()
    return removed


def delete_pantry_item(db: Session, user_id: int, item_id: int) -> bool:
    item = db.get(models.PantryItem, item_id)
    if item is None or item.user_id != user_id:
        return False
    db.delete(item)
    db.commit()
    return True


# ---------------------------------------------------------------- spoilage

def add_spoilage(db: Session, user_id: int, *, item_class: str, storage: str,
                 lifetime_days: float, spoiled: bool) -> models.SpoilageObservation:
    obs = models.SpoilageObservation(
        user_id=user_id, item_class=item_class, storage=storage,
        lifetime_days=lifetime_days, spoiled=spoiled,
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return obs


def observations_for(db: Session, user_id: int) -> list[Observation]:
    rows = db.scalars(select(models.SpoilageObservation)
                      .where(models.SpoilageObservation.user_id == user_id))
    return [Observation(item_class=r.item_class, storage=r.storage,
                        lifetime_days=r.lifetime_days, spoiled=r.spoiled) for r in rows]


# ---------------------------------------------------------------- recipes

def candidate_recipes(db: Session, ids: list[int] | None = None) -> list[CandidateRecipe]:
    q = select(models.Recipe)
    if ids is not None:
        q = q.where(models.Recipe.id.in_(ids))
    return [
        CandidateRecipe(
            id=r.id, name=r.name, course=r.course, diet=r.diet, region=r.region,
            time_mins=r.time_mins, servings=r.servings,
            ingredients=[(i.commodity, i.quantity_g) for i in r.ingredients],
            instructions=r.instructions, source_url=r.source_url,
        )
        for r in db.scalars(q)
    ]


# ---------------------------------------------------------------- prices

def packs_by_source(db: Session, source: str) -> dict[str, list[Pack]]:
    out: dict[str, list[Pack]] = {}
    for row in db.scalars(select(models.PricePack).where(models.PricePack.source == source)):
        out.setdefault(row.commodity, []).append(Pack(size_g=row.pack_size_g, price_rs=row.price_rs))
    return out


def cache_live_packs(db: Session, packs: list[LivePack]) -> None:
    for p in packs:
        existing = db.scalar(select(models.PricePack).where(
            models.PricePack.commodity == p.commodity,
            models.PricePack.pack_size_g == p.pack_size_g,
            models.PricePack.source == "live",
        ))
        if existing:
            existing.price_rs = p.price_rs
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            db.add(models.PricePack(commodity=p.commodity, pack_size_g=p.pack_size_g,
                                    price_rs=p.price_rs, source="live"))
    db.commit()


def price_history(db: Session, commodities: list[str] | None = None) -> dict[str, list[float]]:
    """Chronological price-per-kg series per commodity (all when None)."""
    out: dict[str, list[float]] = {}
    q = select(models.PriceHistory).order_by(models.PriceHistory.commodity, models.PriceHistory.month)
    if commodities is not None:
        q = q.where(models.PriceHistory.commodity.in_(commodities))
    rows = db.scalars(q)
    for r in rows:
        out.setdefault(r.commodity, []).append(r.price_per_kg)
    return out


# ---------------------------------------------------------------- plans

def create_plan(db: Session, user_id: int, request_json: dict,
                state_json: dict, response_json: dict) -> models.Plan:
    plan = models.Plan(
        user_id=user_id,
        request_json=json.dumps(request_json),
        state_json=json.dumps(state_json),
        response_json=json.dumps(response_json),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def get_plan(db: Session, user_id: int, plan_id: int) -> models.Plan | None:
    plan = db.get(models.Plan, plan_id)
    return plan if plan is not None and plan.user_id == user_id else None


def update_plan(db: Session, plan: models.Plan, *, state_json: dict | None = None,
                response_json: dict | None = None) -> None:
    if state_json is not None:
        plan.state_json = json.dumps(state_json)
    if response_json is not None:
        plan.response_json = json.dumps(response_json)
    db.commit()


def replace_meal_log(db: Session, user_id: int, plan_id: int,
                     meals: list[dict]) -> None:
    """meals: [{recipe_id, day, course, cluster}]. Replacing (not appending)
    keeps a regenerated plan from counting as extra weeks of eating."""
    db.execute(delete(models.MealPlanLog).where(models.MealPlanLog.plan_id == plan_id))
    for m in meals:
        db.add(models.MealPlanLog(user_id=user_id, plan_id=plan_id, **m))
    db.commit()


def recent_meal_clusters(db: Session, user_id: int, limit: int = 42) -> list[str]:
    """Most recent meal clusters, oldest first, capped by MEALS not plans."""
    rows = list(db.scalars(
        select(models.MealPlanLog)
        .where(models.MealPlanLog.user_id == user_id)
        .order_by(models.MealPlanLog.id.desc())
        .limit(limit)
    ))
    return [r.cluster for r in reversed(rows)]


# ---------------------------------------------------------------- preferences

def get_preferences(db: Session, user_id: int) -> models.Preference:
    pref = db.get(models.Preference, user_id)
    if pref is None:
        pref = models.Preference(user_id=user_id)
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return pref


def save_preferences(db: Session, user_id: int, *, diet: str, region: str,
                     max_cook_mins: int, dislikes: list[str], family_size: int) -> models.Preference:
    pref = get_preferences(db, user_id)
    pref.diet = diet
    pref.region = region
    pref.max_cook_mins = max_cook_mins
    pref.dislikes_json = json.dumps(dislikes)
    pref.family_size = family_size
    db.commit()
    db.refresh(pref)
    return pref
