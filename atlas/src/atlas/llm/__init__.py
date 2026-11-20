"""Talking to language models (Part 6), behind one small interface.

    chat = make_chat(settings)
    result = chat.complete(ChatRequest(messages=[user("What is an Engine?")], purpose="demo"))
    print(result.text, result.input_tokens, result.output_tokens)

Two providers implement it: Ollama (local, free, the default) and Claude through the
official Anthropic SDK. Code elsewhere in Atlas sees only these neutral types, so the
provider is a setting, not a rewrite. Inside each provider, that provider's own types rule.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


def user(content: str) -> Message:
    return Message("user", content)


@dataclass(frozen=True)
class ChatRequest:
    messages: list[Message]
    system: str = ""
    # A ceiling on the answer's length. Hitting it cuts the answer off mid-sentence:
    # result.stop_reason says so (lesson 6.1).
    max_tokens: int = 1024
    # Ask for JSON matching this model's schema (lesson 6.3). The provider constrains the
    # output; the caller still validates it, because constrained isn't the same as correct.
    output_schema: type[BaseModel] | None = None
    # What the call is for, recorded in the cost log: "fit_score", "answer", ...
    purpose: str = "chat"


# Why generation stopped, in Atlas's words; each provider maps its own onto these.
StopReason = Literal["end", "max_tokens", "refusal", "other"]


@dataclass(frozen=True)
class ChatResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: StopReason
    latency_ms: float
    purpose: str = "chat"


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer, as it is generated."""

    text: str


# A stream yields TextDeltas, then exactly one ChatResult with the totals.
ChatEvent = TextDelta | ChatResult


class ChatProvider(Protocol):
    provider: str
    model: str

    def complete(self, request: ChatRequest) -> ChatResult: ...
    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]: ...


class LLMError(Exception):
    """The model couldn't be reached or answered with an error, after retries."""
