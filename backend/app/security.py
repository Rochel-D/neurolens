"""Auth (bcrypt + JWT), rate limiting, sanitisation and PII redaction."""
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=s.jwt_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        payload = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=[ALGORITHM])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


# ---------- rate limiting (in-memory sliding window; single instance only) ----------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int | None = None, window_s: int = 60) -> None:
    limit = limit or get_settings().rate_limit_per_min
    q = _hits[key]
    t = time.monotonic()
    while q and t - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down a little")
    q.append(t)


def client_key(request: Request, user: User | None = None) -> str:
    return f"u{user.id}" if user else f"ip{request.client.host if request.client else 'x'}"


# ---------- sanitisation ----------
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text: str, max_len: int = 4000) -> str:
    text = _SCRIPT.sub("", text or "")
    text = _TAG.sub("", text)
    text = _CTRL.sub("", text)
    return text.strip()[:max_len]


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)")


def redact(text: str) -> str:
    """Remove obvious PII before text goes to logs or traces."""
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text or ""))
