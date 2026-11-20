from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..schemas import ConsentIn, ProfileIn
from ..security import current_user
from ..services import memory

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
def get_memory(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return memory.long_term(db, user.id, limit=50)


@router.post("/consent")
def consent(body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    memory.set_consent(db, user.id, body.consent)
    return memory.long_term(db, user.id, limit=50)


@router.post("/profile")
def profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        memory.update_profile(db, user.id, body.model_dump())
    except memory.ConsentRequired as e:
        raise HTTPException(409, str(e)) from e
    return memory.long_term(db, user.id, limit=50)


@router.delete("/items/{item_id}")
def delete_item(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not memory.forget_item(db, user.id, item_id):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.delete("/all")
def delete_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    memory.forget_everything(db, user.id)
    return {"ok": True, "message": "Your chats, reports, scans, memory and profile were deleted."}
