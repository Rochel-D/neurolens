from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import LoginIn, RegisterIn
from ..security import client_key, create_token, current_user, hash_password, rate_limit, sanitize, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role}


@router.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    rate_limit("reg:" + client_key(request), limit=10)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    u = User(name=sanitize(body.name, 80), email=email, password_hash=hash_password(body.password), role="user")
    db.add(u)
    db.commit()
    return {"token": create_token(u), "user": _out(u)}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    rate_limit("login:" + client_key(request), limit=10)
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Wrong email or password")  # same message for both cases
    return {"token": create_token(u), "user": _out(u)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _out(user)
