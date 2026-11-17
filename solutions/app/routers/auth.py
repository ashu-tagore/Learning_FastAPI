from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import create_access_token, verify_password

router = APIRouter(tags=["Authentication"])


@router.post("/login", response_model=schemas.Token)
def login(
    # OAuth2 says login is a form post with "username" and "password" fields.
    # We store emails, so the "username" field carries the email.
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(models.User).where(models.User.email == form.username))

    # One message for both cases, so the endpoint can't be used
    # to discover which emails have accounts.
    wrong_credentials = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user is None:
        raise wrong_credentials

    if not verify_password(form.password, user.password_hash):
        raise wrong_credentials

    return {"access_token": create_access_token(user.id), "token_type": "bearer"}
