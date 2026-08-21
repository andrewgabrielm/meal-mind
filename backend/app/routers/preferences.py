"""Household preferences (UX revamp): region, cooking time, dislikes, diet."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import repositories as repo, schemas
from ..db import get_db
from .auth import current_user_id

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _out(pref) -> schemas.PreferenceOut:
    return schemas.PreferenceOut(
        diet=pref.diet, region=pref.region, max_cook_mins=pref.max_cook_mins,
        dislikes=json.loads(pref.dislikes_json), family_size=pref.family_size,
    )


@router.get("", response_model=schemas.PreferenceOut)
def get_preferences(db: Session = Depends(get_db), uid: int = Depends(current_user_id)):
    return _out(repo.get_preferences(db, uid))


@router.put("", response_model=schemas.PreferenceOut)
def put_preferences(body: schemas.PreferenceIn, db: Session = Depends(get_db),
                    uid: int = Depends(current_user_id)):
    return _out(repo.save_preferences(
        db, uid, diet=body.diet, region=body.region,
        max_cook_mins=body.max_cook_mins, dislikes=body.dislikes,
        family_size=body.family_size,
    ))
