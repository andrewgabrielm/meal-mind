"""Pantry endpoints. HTTP only: validate, delegate, return."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import presenter, repositories as repo, schemas
from ..db import get_db
from ..services import decay
from .auth import current_user_id

router = APIRouter(prefix="/pantry", tags=["pantry"])


@router.get("", response_model=list[schemas.PantryItemOut])
def list_items(db: Session = Depends(get_db), uid: int = Depends(current_user_id)):
    return repo.list_pantry(db, uid)


@router.post("", response_model=schemas.PantryItemOut, status_code=201)
def add_item(body: schemas.PantryItemIn, db: Session = Depends(get_db),
             uid: int = Depends(current_user_id)):
    return repo.add_pantry_item(
        db, uid, commodity=body.commodity, quantity_g=body.quantity_g,
        storage=body.storage, purchased_on=body.purchased_on or date.today(),
        item_class=body.item_class,
    )


@router.delete("/{item_id}", status_code=204)
def remove_item(item_id: int, db: Session = Depends(get_db),
                uid: int = Depends(current_user_id)):
    if not repo.delete_pantry_item(db, uid, item_id):
        raise HTTPException(status_code=404, detail="pantry item not found")


@router.post("/spoilage", status_code=201)
def report_spoilage(body: schemas.SpoilageIn, db: Session = Depends(get_db),
                    uid: int = Depends(current_user_id)):
    """Outcome report feeding the conjugate posterior — the model learns from
    these."""
    obs = repo.add_spoilage(
        db, uid, item_class=body.item_class, storage=body.storage,
        lifetime_days=body.lifetime_days, spoiled=body.spoiled,
    )
    return {"id": obs.id, "recorded": True}


@router.get("/decay", response_model=schemas.DecayAssessmentOut)
def decay_assessment(db: Session = Depends(get_db), uid: int = Depends(current_user_id)):
    assessments = decay.assess_pantry(
        repo.pantry_as_dicts(db, uid),
        repo.observations_for(db, uid),
    )
    return presenter.decay_out(assessments)
