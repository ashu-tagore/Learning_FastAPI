from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user

router = APIRouter(prefix="/posts", tags=["Posts"])


def posts_with_vote_counts():
    """SELECT posts.*, COUNT(votes.post_id) ... LEFT JOIN votes ... GROUP BY posts.id

    LEFT (outer) join, because an inner join would drop posts with zero votes.
    """
    vote_count = func.count(models.Vote.post_id).label("votes")
    return (
        select(models.Post, vote_count)
        .outerjoin(models.Vote, models.Vote.post_id == models.Post.id)
        .group_by(models.Post.id)
    )


def get_owned_post(post_id: int, user: models.User, db: Session) -> models.Post:
    """Fetch a post the current user is allowed to modify, or raise."""
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Post {post_id} not found")

    # 403, not 404: the post exists, you are logged in, you just don't own it.
    if post.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your post")

    return post


@router.get("/", response_model=list[schemas.PostWithVotes])
def list_posts(
    db: Session = Depends(get_db),
    limit: int = Query(default=10, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    search: str = "",
):
    query = (
        posts_with_vote_counts()
        .where(models.Post.title.contains(search))
        .order_by(models.Post.created_at.desc())
        .limit(limit)
        .offset(skip)
    )
    rows = db.execute(query).all()
    return [{"post": post, "votes": votes} for post, votes in rows]


@router.get("/{post_id}", response_model=schemas.PostWithVotes)
def get_post(post_id: int, db: Session = Depends(get_db)):
    query = posts_with_vote_counts().where(models.Post.id == post_id)
    row = db.execute(query).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Post {post_id} not found")

    post, votes = row
    return {"post": post, "votes": votes}


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=schemas.PostOut)
def create_post(
    post_in: schemas.PostCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # owner_id comes from the token, never from the request body,
    # otherwise anyone could post on behalf of anyone else.
    post = models.Post(**post_in.model_dump(), owner_id=user.id)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@router.patch("/{post_id}", response_model=schemas.PostOut)
def update_post(
    post_id: int,
    changes: schemas.PostUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    post = get_owned_post(post_id, user, db)

    # exclude_unset keeps fields the client didn't send out of the update,
    # which is the whole difference between PATCH and PUT.
    for field, value in changes.model_dump(exclude_unset=True).items():
        setattr(post, field, value)

    db.commit()
    db.refresh(post)
    return post


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    post = get_owned_post(post_id, user, db)
    db.delete(post)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
