from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user

router = APIRouter(prefix="/votes", tags=["Votes"])


@router.post("/", status_code=status.HTTP_201_CREATED)
def vote(
    vote_in: schemas.VoteIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    post = db.get(models.Post, vote_in.post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Post {vote_in.post_id} not found")

    # The primary key is (user_id, post_id), so db.get takes both.
    existing_vote = db.get(models.Vote, (user.id, vote_in.post_id))

    if vote_in.direction == "up":
        if existing_vote is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Already voted on this post")
        db.add(models.Vote(user_id=user.id, post_id=vote_in.post_id))
        db.commit()
        return {"message": "Vote added"}

    if existing_vote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vote does not exist")
    db.delete(existing_vote)
    db.commit()
    return {"message": "Vote removed"}
