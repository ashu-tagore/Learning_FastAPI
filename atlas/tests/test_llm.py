"""The provider interface: Ollama and Claude behind the same neutral types (lesson 6.2)."""

from decimal import Decimal
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.llm import ChatRequest, ChatResult, LLMError, TextDelta, user
from atlas.llm.calls import cost_usd, record
from atlas.llm.claude import ClaudeChat
from atlas.llm.ollama import OllamaChat
from atlas.models import LlmCall
from tests.fake_llm import FakeOllama, claude_message, claude_transport


class Answer(BaseModel):
    score: int


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def local_chat(ollama: FakeOllama, monkeypatch: pytest.MonkeyPatch) -> OllamaChat:
    monkeypatch.setattr("atlas.llm.ollama.time.sleep", lambda seconds: None)  # no real waits
    return OllamaChat("qwen3:1.7b", transport=ollama.transport())


def request(**overrides: Any) -> ChatRequest:
    return ChatRequest(messages=[user("What is an Engine?")], purpose="test", **overrides)


# ---------- Ollama ----------


def test_ollama_answer_comes_back_in_atlas_types(
    local_chat: OllamaChat, ollama: FakeOllama
) -> None:
    ollama.replies.append("It runs the training loop.")

    result = local_chat.complete(request(system="Be brief."))

    assert (result.text, result.stop_reason, result.purpose) == (
        "It runs the training loop.",
        "end",
        "test",
    )
    assert (result.input_tokens, result.output_tokens) == (42, 5)
    sent = ollama.requests[0]
    assert sent["messages"][0] == {"role": "system", "content": "Be brief."}
    assert sent["options"]["num_predict"] == 1024


def test_a_cut_off_answer_says_so(local_chat: OllamaChat, ollama: FakeOllama) -> None:
    ollama.replies.append("It runs the")
    ollama.done_reason = "length"

    assert local_chat.complete(request()).stop_reason == "max_tokens"


def test_structured_output_sends_the_schema(local_chat: OllamaChat, ollama: FakeOllama) -> None:
    ollama.replies.append('{"score": 4}')

    result = local_chat.complete(request(output_schema=Answer))

    assert ollama.requests[0]["format"]["properties"] == {
        "score": {"title": "Score", "type": "integer"}
    }
    assert Answer.model_validate_json(result.text).score == 4


def test_streaming_yields_pieces_then_the_totals(
    local_chat: OllamaChat, ollama: FakeOllama
) -> None:
    ollama.replies.append("It runs the loop.")

    events = list(local_chat.stream(request()))

    pieces = [event.text for event in events if isinstance(event, TextDelta)]
    assert pieces == ["It", " runs", " the", " loop."]
    assert isinstance(events[-1], ChatResult)
    assert events[-1].text == "It runs the loop."


def test_a_busy_server_is_retried(local_chat: OllamaChat, ollama: FakeOllama) -> None:
    ollama.failures = [503, 503]
    ollama.replies.append("Fine now.")

    assert local_chat.complete(request()).text == "Fine now."
    assert len(ollama.requests) == 3


def test_giving_up_raises_a_clear_error(local_chat: OllamaChat, ollama: FakeOllama) -> None:
    ollama.failures = [503, 503, 503]

    with pytest.raises(LLMError, match="Ollama answered 503"):
        local_chat.complete(request())


def test_an_unreachable_server_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("atlas.llm.ollama.time.sleep", lambda seconds: None)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    chat = OllamaChat("qwen3:1.7b", transport=httpx.MockTransport(refuse))

    with pytest.raises(LLMError, match="cannot reach Ollama"):
        chat.complete(request())


# ---------- Claude ----------


def claude(respond: Any) -> ClaudeChat:
    client = anthropic.Anthropic(
        api_key="test-key",  # never sent anywhere: the transport answers in memory
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=claude_transport(respond)),
    )
    return ClaudeChat(client=client)


def test_claude_request_carries_effort_and_refusal_fallbacks() -> None:
    seen: list[dict[str, Any]] = []

    def respond(body: dict[str, Any], http: httpx2.Request) -> httpx2.Response:
        seen.append({**body, "beta": http.headers.get("anthropic-beta")})
        return httpx2.Response(200, json=claude_message("It runs the training loop."))

    result = claude(respond).complete(request(system="Be brief."))

    assert result.text == "It runs the training loop."
    assert (result.provider, result.input_tokens, result.output_tokens) == ("anthropic", 1200, 300)
    sent = seen[0]
    assert (sent["model"], sent["system"], sent["output_config"]) == (
        "claude-opus-5-5",
        "Be brief.",
        {"effort": "medium"},
    )
    assert (sent["fallbacks"], sent["beta"]) == ("default", "server-side-fallback-2026-07-01")


def test_claude_structured_output_goes_in_output_config() -> None:
    seen: list[dict[str, Any]] = []

    def respond(body: dict[str, Any], http: httpx2.Request) -> httpx2.Response:
        seen.append(body)
        return httpx2.Response(200, json=claude_message('{"score": 4}'))

    result = claude(respond).complete(request(output_schema=Answer))

    assert Answer.model_validate_json(result.text).score == 4
    output_format = seen[0]["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["schema"]["additionalProperties"] is False
    assert seen[0]["output_config"]["effort"] == "medium"


def test_claude_refusal_is_reported_not_hidden() -> None:
    def respond(body: dict[str, Any], http: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=claude_message("", stop_reason="refusal"))

    assert claude(respond).complete(request()).stop_reason == "refusal"


def test_claude_errors_become_llm_errors() -> None:
    def respond(body: dict[str, Any], http: httpx2.Request) -> httpx2.Response:
        error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        return httpx2.Response(529, json=error)

    with pytest.raises(LLMError, match="Claude request failed"):
        claude(respond).complete(request())


# ---------- Cost log ----------


def result(provider: str, model: str) -> ChatResult:
    return ChatResult("text", provider, model, 1200, 300, "end", 950.0, "answer")


def test_cost_comes_from_the_price_list() -> None:
    # 1,200 input tokens at $4 per million plus 300 output tokens at $20 per million.
    assert cost_usd(result("anthropic", "claude-opus-5-5")) == Decimal("0.010800")
    assert cost_usd(result("ollama", "qwen3:1.7b")) == Decimal(0)
    assert cost_usd(result("anthropic", "claude-model-from-the-future")) is None


def test_calls_are_recorded(session: Session) -> None:
    record(session, result("anthropic", "claude-opus-5-5"))
    session.flush()

    call = session.scalars(select(LlmCall)).one()
    assert (call.purpose, call.cost_usd, call.stop_reason) == ("answer", Decimal("0.010800"), "end")


def test_claude_without_credentials_says_how_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (lesson 6.2): the SDK raised a bare TypeError at request time, and
    # `atlas llm` printed a traceback instead of a message.
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")  # no `ant auth login` profile either

    def respond(body: dict[str, Any], http: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=claude_message("unreachable"))

    client = anthropic.Anthropic(
        http_client=anthropic.DefaultHttpxClient(transport=claude_transport(respond))
    )

    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        ClaudeChat(client=client).complete(request())
