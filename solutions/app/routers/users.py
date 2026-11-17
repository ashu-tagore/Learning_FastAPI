from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user, hash_password

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=schemas.UserOut)
def create_user(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(models.User).where(models.User.email == user_in.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = models.User(email=user_in.email, password_hash=hash_password(user_in.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    # response_model=UserOut strips password_hash before this leaves the server.
    return user


# Declared before /{user_id} so "me" isn't parsed as an id.
@router.get("/me", response_model=schemas.UserOut)
def read_me(user: models.User = Depends(get_current_user)):
    return user


@router.get("/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    return user
