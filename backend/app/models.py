"""SQLAlchemy tables. All access goes through repositories.py — never from
services or routers."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PantryItem(Base):
    __tablename__ = "pantry_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    commodity: Mapped[str] = mapped_column(String(64), index=True)
    quantity_g: Mapped[float] = mapped_column(Float)
    item_class: Mapped[str] = mapped_column(String(32))          # leafy_green, vegetable, dairy, staple, ...
    storage: Mapped[str] = mapped_column(String(16), default="room")  # room | fridge | freezer
    purchased_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SpoilageObservation(Base):
    """Outcome reports feeding the conjugate posterior in services/decay.py.

    lifetime_days is the *actual* elapsed days; the room-equivalent conversion
    happens in the service (storage recorded here so it can)."""
    __tablename__ = "spoilage_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    item_class: Mapped[str] = mapped_column(String(32), index=True)
    storage: Mapped[str] = mapped_column(String(16), default="room")
    lifetime_days: Mapped[float] = mapped_column(Float)
    spoiled: Mapped[bool] = mapped_column(Boolean)   # False = consumed while still good (censored)
    observed_on: Mapped[date] = mapped_column(Date, default=date.today)


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    course: Mapped[str] = mapped_column(String(32), index=True)   # breakfast | lunch | dinner | side | snack
    diet: Mapped[str] = mapped_column(String(32), index=True)     # vegetarian | eggetarian | non_vegetarian | vegan
    region: Mapped[str] = mapped_column(String(48), default="")   # north | south | east | west | "" = pan-indian
    time_mins: Mapped[int] = mapped_column(Integer, default=30)
    servings: Mapped[int] = mapped_column(Integer, default=4)
    instructions: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(400), default="")
    source: Mapped[str] = mapped_column(String(16), default="seed")  # seed | ingest

    ingredients: Mapped[list[RecipeIngredient]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", lazy="selectin"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(64), index=True)  # canonical commodity key
    quantity_g: Mapped[float] = mapped_column(Float)                # grams for recipe.servings
    raw_text: Mapped[str] = mapped_column(String(300), default="")

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")


class PricePack(Base):
    """Purchasable pack of a commodity with a price. source: seed | cache | live."""
    __tablename__ = "price_packs"
    __table_args__ = (UniqueConstraint("commodity", "pack_size_g", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    commodity: Mapped[str] = mapped_column(String(64), index=True)
    pack_size_g: Mapped[float] = mapped_column(Float)
    price_rs: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="seed", index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PriceHistory(Base):
    """Monthly historical retail prices (WFP series) — training data for the
    ARIMA/GARCH volatility model in services/forecast.py."""
    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("commodity", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    commodity: Mapped[str] = mapped_column(String(64), index=True)
    month: Mapped[date] = mapped_column(Date)          # first of month
    price_per_kg: Mapped[float] = mapped_column(Float)


class Plan(Base):
    """A generated plan, persisted so /plans/{id}/pantry and /plans/{id}/swap
    can re-solve stages 4-7 against the frozen recipe selection."""
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    request_json: Mapped[str] = mapped_column(Text)    # PlanRequest as sent
    state_json: Mapped[str] = mapped_column(Text)      # meals, ticked pantry, locks
    response_json: Mapped[str] = mapped_column(Text)   # last full PlanResponse


class MealPlanLog(Base):
    """One row per planned meal — the variety model's rolling history."""
    __tablename__ = "meal_plan_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True)
    recipe_id: Mapped[int] = mapped_column(Integer)
    day: Mapped[int] = mapped_column(Integer)
    course: Mapped[str] = mapped_column(String(32))
    cluster: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Preference(Base):
    """Household preferences (UX revamp): region, cooking time, dislikes."""
    __tablename__ = "preferences"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    diet: Mapped[str] = mapped_column(String(32), default="vegetarian")
    region: Mapped[str] = mapped_column(String(48), default="")
    max_cook_mins: Mapped[int] = mapped_column(Integer, default=60)
    dislikes_json: Mapped[str] = mapped_column(Text, default="[]")  # commodity keys
    family_size: Mapped[int] = mapped_column(Integer, default=4)
