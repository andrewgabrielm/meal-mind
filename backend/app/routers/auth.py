"""User creation, login, and the auth dependency every other router uses.

Tokens are stateless HS256 JWTs (services/auth.py, stdlib only). Logout is
client-side (drop the token). 30-day expiry — it's a household app.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .. import repositories as repo, schemas
from ..config import get_settings
from ..db import get_db
from ..services.auth import hash_password, make_token, parse_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


def current_user_id(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> int:
    """FastAPI dependency: Authorization: Bearer <jwt> -> user id, or 401."""
    if cred is None:
        raise HTTPException(status_code=401, detail="not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = parse_token(cred.credentials, get_settings().jwt_secret)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e),
                            headers={"WWW-Authenticate": "Bearer"})
    if repo.get_user(db, user_id) is None:
        raise HTTPException(status_code=401, detail="unknown user",
                            headers={"WWW-Authenticate": "Bearer"})
    return user_id


def _token_out(user) -> schemas.TokenOut:
    return schemas.TokenOut(
        token=make_token(user.id, get_settings().jwt_secret),
        user=schemas.UserOut(id=user.id, email=user.email, name=user.name),
    )


@router.post("/register", response_model=schemas.TokenOut, status_code=201)
def register(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    if repo.user_by_email(db, body.email) is not None:
        raise HTTPException(status_code=409, detail="an account with this email exists")
    user = repo.create_user(db, email=body.email, name=body.name,
                            password_hash=hash_password(body.password))
    return _token_out(user)


@router.post("/login", response_model=schemas.TokenOut)
def login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    user = repo.user_by_email(db, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        # one message for both cases: never reveal which emails exist
        raise HTTPException(status_code=401, detail="wrong email or password")
    return _token_out(user)


@router.get("/me", response_model=schemas.UserOut)
def me(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    user = repo.get_user(db, user_id)
    return schemas.UserOut(id=user.id, email=user.email, name=user.name)
