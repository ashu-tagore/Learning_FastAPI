"""Explain this file (lesson 6.9): the acceptance tests from the build task."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models import LlmCall
from tests.conftest import sign_up
from tests.fake_llm import FakeOllama
from tests.test_api_resources import index_sample
from tests.test_ask import sse_events


async def explain(client: httpx.AsyncClient, headers: dict[str, str], path: str) -> httpx.Response:
    return await client.post("/explain", json={"path": path}, headers=headers)


@pytest.mark.anyio
async def test_a_file_is_explained_from_its_own_chunks_in_line_order(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    fake_ollama: FakeOllama,
    sample_repo: Path,
) -> None:
    await index_sample(async_session, sample_repo)
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("Engine runs the loop [2] and is a class [1].")

    response = await explain(client, headers, "ignite/engine/engine.py")

    assert response.status_code == 200
    events = sse_events(response.text)
    sources = events[0][1]
    assert [(s["number"], s["symbol"]) for s in sources] == [(1, "Engine"), (2, "Engine.run")]
    assert events[-1] == (
        "done",
        {"cited": [2, 1], "invalid_citations": [], "stop_reason": "end", "truncated": False},
    )
    prompt = fake_ollama.requests[0]["messages"][-1]["content"]
    assert "Explain what ignite/engine/engine.py does" in prompt
    call = await async_session.scalar(select(LlmCall))
    assert call is not None
    assert call.purpose == "explain"


@pytest.mark.anyio
async def test_a_file_that_isnt_indexed_is_a_404_before_any_streaming(
    client: httpx.AsyncClient, async_session: AsyncSession, sample_repo: Path
) -> None:
    await index_sample(async_session, sample_repo)
    headers = await sign_up(client, "ada@example.com")

    response = await explain(client, headers, "ignite/nowhere.py")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


@pytest.mark.anyio
async def test_a_long_file_is_explained_from_its_start_and_says_so(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    fake_ollama: FakeOllama,
    tmp_path: Path,
) -> None:
    functions = "\n\n".join(f"def step_{n}():\n    return {n}" for n in range(8))
    (tmp_path / "steps.py").write_text(functions + "\n")
    await index_sample(async_session, tmp_path)
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("The first steps return numbers [1].")

    response = await explain(client, headers, "steps.py")

    events = sse_events(response.text)
    assert len(events[0][1]) == 6
    assert events[-1][1]["truncated"] is True
    prompt = fake_ollama.requests[0]["messages"][-1]["content"]
    assert "The sources cover only the start of the file" in prompt


@pytest.mark.anyio
async def test_explaining_needs_a_signed_in_user(client: httpx.AsyncClient) -> None:
    response = await client.post("/explain", json={"path": "ignite/engine/engine.py"})

    assert response.status_code == 401
