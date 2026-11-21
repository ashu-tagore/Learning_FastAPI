"""Questions answered from the code, streamed as Server-Sent Events (lesson 6.7).

    POST /ask                          stream an answer, with its sources and citations
    GET  /conversations                my conversations, most recent first
    GET  /conversations/{id}           one conversation's questions and answers
    POST /answers/{id}/feedback        rate an answer, thumbs up or down
    POST /explain                      stream an explanation of one file (lesson 6.9)

The stream is a sequence of named events:

    event: sources   the numbered chunks the model was given (before it starts writing)
    event: delta     a piece of the answer's text, as the model writes it
    event: done      where the answer was stored, and what it cited
    event: error     the model couldn't be reached; nothing was stored
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from atlas.answer import (
    NOT_FOUND,
    Source,
    Turn,
    answer_request,
    check_citations,
    explain_question,
    file_sources,
    find_sources,
    github_url,
    retrieval_text,
)
from atlas.api.deps import ChatDep, CurrentUser, EmbedderDep, SessionDep
from atlas.api.errors import ApiError
from atlas.api.limits import limit_per_user
from atlas.api.schemas import (
    AnswerDone,
    AskIn,
    CitationOut,
    ConversationDetail,
    ConversationOut,
    ExplainDone,
    ExplainIn,
    FeedbackIn,
    FeedbackOut,
    MessageOut,
    SourceOut,
)
from atlas.llm import ChatResult, LLMError, TextDelta
from atlas.llm.calls import record
from atlas.models import Citation, Conversation, Feedback, Message, Repo

router = APIRouter(tags=["ask"])

# Each answer keeps a model busy for 10 to 60 seconds on a CPU; a person asks a few a minute.
ASKS_PER_MINUTE = 10


@dataclass(frozen=True)
class AskTarget:
    """What a question is about, checked before the stream starts."""

    user_id: int
    repo: str
    conversation_id: int | None
    history: list[Turn]


async def ask_target(body: AskIn, user: CurrentUser, session: SessionDep) -> AskTarget:
    """Check the conversation and repository exist, and load the conversation so far.

    A dependency, not the start of the route, for a reason: once a streaming response has
    begun, its status is already 200. Errors that should be a 404 must happen before.
    """
    repo = body.repo
    history: list[Turn] = []
    if body.conversation_id is not None:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == body.conversation_id, Conversation.user_id == user.id
            )
        )
        if conversation is None:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "conversation_not_found", "No such conversation"
            )
        repo = conversation.repo_full_name
        messages = await session.scalars(
            select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
        )
        history = turns(list(messages))

    is_indexed = await session.scalar(
        select(Repo.id).where(Repo.full_name == repo, Repo.indexed_at.is_not(None))
    )
    if is_indexed is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "repo_not_found", f"{repo} isn't indexed")
    return AskTarget(user.id, repo, body.conversation_id, history)


def turns(messages: list[Message]) -> list[Turn]:
    """Pair each question with the answer after it."""
    pairs = []
    for question, answer in zip(messages[::2], messages[1::2], strict=False):
        pairs.append(Turn(question.content, answer.content))
    return pairs


@router.post(
    "/ask",
    response_class=EventSourceResponse,
    dependencies=[Depends(limit_per_user("ask", ASKS_PER_MINUTE))],
    responses={404: {"description": "No such conversation, or the repository isn't indexed"}},
)
async def ask(
    body: AskIn,
    target: Annotated[AskTarget, Depends(ask_target)],
    session: SessionDep,
    embedder: EmbedderDep,
    chat: ChatDep,
) -> AsyncIterator[ServerSentEvent]:
    """Answer a question from the indexed code, streaming the answer as it is written."""
    search_text = retrieval_text(body.question, target.history)
    try:
        # Both model clients are synchronous: threads keep them off the event loop.
        query_vector = (await run_in_threadpool(embedder.embed, [search_text], "query"))[0]
    except LLMError:
        yield model_unavailable()
        return

    sources = await session.run_sync(
        lambda sync: find_sources(sync, target.repo, body.question, query_vector, embedder.model)
    )
    yield ServerSentEvent(
        event="sources", data=[source_out(target.repo, source) for source in sources]
    )

    parts: list[str] = []
    result: ChatResult | None = None
    if not sources:
        # No citation, no claim: nothing relevant was found, so no model is asked to guess.
        parts.append(NOT_FOUND)
        yield ServerSentEvent(event="delta", data={"text": NOT_FOUND})
    else:
        request = answer_request(body.question, sources, target.history)
        try:
            async for event in iterate_in_threadpool(chat.stream(request)):
                if isinstance(event, TextDelta):
                    parts.append(event.text)
                    yield ServerSentEvent(event="delta", data={"text": event.text})
                else:
                    result = event
        except LLMError:
            # The client has seen part of an answer; the error event tells it to discard it.
            yield model_unavailable()
            return

    text = "".join(parts)
    cited, invalid = check_citations(text, sources)
    done = await save_exchange(session, target, body.question, text, cited, invalid, result)
    yield ServerSentEvent(event="done", data=done)


def model_unavailable() -> ServerSentEvent:
    message = "The language model isn't reachable right now. Try again in a minute."
    return ServerSentEvent(event="error", data={"code": "model_unavailable", "message": message})


def source_out(repo: str, source: Source) -> SourceOut:
    return SourceOut(
        number=source.number,
        path=source.path,
        start_line=source.start_line,
        end_line=source.end_line,
        symbol=source.symbol,
        url=github_url(repo, source.commit, source.path, source.start_line, source.end_line),
    )


async def save_exchange(
    session: AsyncSession,
    target: AskTarget,
    question: str,
    answer: str,
    cited: list[Source],
    invalid: list[int],
    result: ChatResult | None,
) -> AnswerDone:
    """Store the question, the answer, its citations and the model call, in one transaction.

    Stored only once the answer is complete: a stream cut off halfway leaves nothing behind.
    """
    if target.conversation_id is None:
        conversation = Conversation(
            user_id=target.user_id, repo_full_name=target.repo, title=question[:200]
        )
        session.add(conversation)
    else:
        conversation = await session.get_one(Conversation, target.conversation_id)
        conversation.updated_at = func.now()

    llm_call_id = None
    if result is not None:
        call = await session.run_sync(lambda sync: record(sync, result))
        await session.flush()
        llm_call_id = call.id
    await session.flush()  # gives a new conversation its id

    session.add(Message(conversation_id=conversation.id, role="user", content=question))
    answer_message = Message(
        conversation_id=conversation.id, role="assistant", content=answer, llm_call_id=llm_call_id
    )
    session.add(answer_message)
    await session.flush()
    for source in cited:
        session.add(
            Citation(
                message_id=answer_message.id,
                number=source.number,
                path=source.path,
                start_line=source.start_line,
                end_line=source.end_line,
                symbol=source.symbol,
                commit=source.commit,
            )
        )
    await session.commit()
    return AnswerDone(
        conversation_id=conversation.id,
        message_id=answer_message.id,
        cited=[source.number for source in cited],
        invalid_citations=invalid,
        stop_reason=result.stop_reason if result is not None else "not_found",
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(user: CurrentUser, session: SessionDep) -> list[Conversation]:
    conversations = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(50)
    )
    return list(conversations)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    responses={404: {"description": "No such conversation"}},
)
async def get_conversation(
    conversation_id: int, user: CurrentUser, session: SessionDep
) -> ConversationDetail:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .options(
            selectinload(Conversation.messages).selectinload(Message.citations),
            selectinload(Conversation.messages).selectinload(Message.feedback),
        )
    )
    if conversation is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "conversation_not_found", "No such conversation")

    repo = conversation.repo_full_name
    messages = []
    for message in conversation.messages:
        citations = [
            CitationOut(
                number=citation.number,
                path=citation.path,
                start_line=citation.start_line,
                end_line=citation.end_line,
                symbol=citation.symbol,
                url=github_url(
                    repo, citation.commit, citation.path, citation.start_line, citation.end_line
                ),
            )
            for citation in message.citations
        ]
        rating = message.feedback.rating if message.feedback else None
        messages.append(
            MessageOut(
                id=message.id,
                role=message.role,  # type: ignore[arg-type]  # the CHECK constraint holds it
                content=message.content,
                created_at=message.created_at,
                citations=citations,
                rating=rating,  # type: ignore[arg-type]
            )
        )
    summary = ConversationOut.model_validate(conversation)
    return ConversationDetail(**summary.model_dump(), messages=messages)


@router.post(
    "/answers/{message_id}/feedback",
    response_model=FeedbackOut,
    responses={404: {"description": "No such answer"}},
)
async def rate_answer(
    message_id: int, body: FeedbackIn, user: CurrentUser, session: SessionDep
) -> Feedback:
    """Rate an answer; rating it again replaces the earlier rating."""
    is_my_answer = await session.scalar(
        select(Message.id)
        .join(Message.conversation)
        .where(
            Message.id == message_id,
            Message.role == "assistant",
            Conversation.user_id == user.id,
        )
    )
    if is_my_answer is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "answer_not_found", "No such answer")

    feedback: Feedback | None = await session.scalar(
        insert(Feedback)
        .values(message_id=message_id, rating=body.rating, note=body.note)
        .on_conflict_do_update(
            index_elements=["message_id"], set_={"rating": body.rating, "note": body.note}
        )
        .returning(Feedback)
    )
    await session.commit()
    assert feedback is not None  # an upsert always returns its row
    return feedback


@dataclass(frozen=True)
class FileToExplain:
    sources: list[Source]
    truncated: bool


async def file_to_explain(body: ExplainIn, user: CurrentUser, session: SessionDep) -> FileToExplain:
    """The file's chunks, loaded before the stream starts, so a missing file is a 404."""
    sources, truncated = await session.run_sync(
        lambda sync: file_sources(sync, body.repo, body.path)
    )
    if not sources:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "file_not_found", f"{body.path} isn't indexed in {body.repo}"
        )
    return FileToExplain(sources, truncated)


@router.post(
    "/explain",
    response_class=EventSourceResponse,
    dependencies=[Depends(limit_per_user("explain", ASKS_PER_MINUTE))],
    responses={404: {"description": "The file isn't indexed"}},
)
async def explain(
    body: ExplainIn,
    target: Annotated[FileToExplain, Depends(file_to_explain)],
    session: SessionDep,
    chat: ChatDep,
) -> AsyncIterator[ServerSentEvent]:
    """Explain one file from its own chunks, streamed like an answer, with citations."""
    sources = target.sources
    yield ServerSentEvent(
        event="sources", data=[source_out(body.repo, source) for source in sources]
    )
    question = explain_question(body.path, target.truncated)
    # The same prompt as an answer; logged under its own purpose, for the cost breakdown.
    request = replace(answer_request(question, sources, history=[]), purpose="explain")
    parts: list[str] = []
    result: ChatResult | None = None
    try:
        async for event in iterate_in_threadpool(chat.stream(request)):
            if isinstance(event, TextDelta):
                parts.append(event.text)
                yield ServerSentEvent(event="delta", data={"text": event.text})
            else:
                result = event
    except LLMError:
        yield model_unavailable()
        return

    assert result is not None  # a stream ends with its ChatResult
    await session.run_sync(lambda sync: record(sync, result))
    await session.commit()
    cited, invalid = check_citations("".join(parts), sources)
    done = ExplainDone(
        cited=[source.number for source in cited],
        invalid_citations=invalid,
        stop_reason=result.stop_reason,
        truncated=target.truncated,
    )
    yield ServerSentEvent(event="done", data=done)
