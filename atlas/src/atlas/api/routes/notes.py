"""Private notes on issues (the review lesson's unguided build, 4.16).

A note points at repository + issue number rather than at the issue row, so it survives
the issue closing and being removed from the issues table (lesson 3.4's stretch).
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from atlas.api import idempotency
from atlas.api.deps import CurrentUser, SessionDep
from atlas.api.routes.issues import get_issue_or_404
from atlas.api.schemas import NoteCreate, NoteOut, NoteUpdate
from atlas.models import IssueNote, Repo, User

router = APIRouter(tags=["notes"])


def note_out(note: IssueNote, repo_full_name: str) -> NoteOut:
    return NoteOut(
        id=note.id,
        repo=repo_full_name,
        issue_number=note.issue_number,
        body=note.body,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def get_own_note(session: SessionDep, note_id: int, user: User) -> IssueNote:
    note = session.get(IssueNote, note_id)
    # Someone else's note gets the same 404 as a missing one: no hint that it exists.
    if note is None or note.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Note {note_id} not found")
    return note


def repo_name(session: SessionDep, repo_id: int) -> str:
    repo = session.get(Repo, repo_id)
    assert repo is not None  # foreign key guarantees it
    return repo.full_name


@router.post(
    "/issues/{issue_id}/notes", status_code=status.HTTP_201_CREATED, response_model=NoteOut
)
def create_note(
    issue_id: int,
    body: NoteCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> JSONResponse:
    previous = idempotency.replay(session, user, idempotency_key, request.url.path)
    if previous is not None:
        return previous

    issue = get_issue_or_404(session, issue_id)
    note = IssueNote(
        user_id=user.id, repo_id=issue.repo_id, issue_number=issue.number, body=body.body
    )
    session.add(note)
    session.flush()
    session.refresh(note)
    payload = note_out(note, repo_name(session, issue.repo_id)).model_dump(mode="json")
    idempotency.remember(
        session, user, idempotency_key, request.url.path, status.HTTP_201_CREATED, payload
    )
    session.commit()
    return JSONResponse(payload, status_code=status.HTTP_201_CREATED)


@router.get("/issues/{issue_id}/notes", response_model=list[NoteOut])
def list_notes(issue_id: int, user: CurrentUser, session: SessionDep) -> list[NoteOut]:
    issue = get_issue_or_404(session, issue_id)
    notes = session.scalars(
        select(IssueNote)
        .where(
            IssueNote.user_id == user.id,
            IssueNote.repo_id == issue.repo_id,
            IssueNote.issue_number == issue.number,
        )
        .order_by(IssueNote.created_at)
    )
    name = repo_name(session, issue.repo_id)
    return [note_out(note, name) for note in notes]


@router.patch("/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: int, body: NoteUpdate, user: CurrentUser, session: SessionDep) -> NoteOut:
    note = get_own_note(session, note_id, user)
    note.body = body.body
    session.commit()
    session.refresh(note)
    return note_out(note, repo_name(session, note.repo_id))


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, user: CurrentUser, session: SessionDep) -> Response:
    note = get_own_note(session, note_id, user)
    session.delete(note)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
