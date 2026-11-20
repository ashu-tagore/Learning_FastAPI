"""Claude through the official Anthropic Python SDK (lesson 6.2).

The SDK handles authentication (ANTHROPIC_API_KEY, or an `ant auth login` profile),
retries of transient failures (connection errors, 408, 409, 429, 5xx; two by default) and
timeouts. This class only translates between Atlas's neutral types and the SDK's.

Requests go to the beta endpoint for one feature: server-side refusal fallbacks. If a
model's safety classifier declines a request, the API re-runs it on a fallback model inside
the same call, instead of returning nothing.
"""

import time
from collections.abc import Iterator
from typing import Any

import anthropic
from anthropic.types.beta import BetaMessage

from atlas.llm import ChatEvent, ChatRequest, ChatResult, LLMError, StopReason, TextDelta

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models whose refusals can fall back server-side; Haiku's can't (it has no fallback).
MODELS_WITH_FALLBACK = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2")


class ClaudeChat:
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        # Effort trades depth of thinking against tokens and time. Claude Opus 5.5 defaults
        # to "medium" anyway; saying so keeps the choice visible and in one place.
        self.effort = effort
        # No key in the code: the SDK reads ANTHROPIC_API_KEY or an `ant auth login` profile.
        self.client = client or anthropic.Anthropic()

    def complete(self, request: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        try:
            if request.output_schema is not None:
                # parse() turns the Pydantic model into a schema Claude accepts (moving
                # constraints such as minimum and maximum into client-side checks).
                message = self.client.beta.messages.parse(
                    **self._params(request), output_format=request.output_schema
                )
            else:
                message = self.client.beta.messages.create(**self._params(request))
        except anthropic.APIError as error:
            # Raised only after the SDK's own retries have given up.
            raise LLMError(f"Claude request failed: {error}") from error
        except TypeError as error:
            raise_if_no_credentials(error)
            raise
        return self._result(request, message, started)

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        started = time.perf_counter()
        extra: dict[str, Any] = {}
        if request.output_schema is not None:
            extra["output_format"] = request.output_schema
        try:
            with self.client.beta.messages.stream(**self._params(request), **extra) as stream:
                for text in stream.text_stream:
                    yield TextDelta(text)
                message = stream.get_final_message()
        except anthropic.APIError as error:
            raise LLMError(f"Claude request failed: {error}") from error
        except TypeError as error:
            raise_if_no_credentials(error)
            raise
        yield self._result(request, message, started)

    def _params(self, request: ChatRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "output_config": {"effort": self.effort},
        }
        if request.system:
            params["system"] = request.system
        if self.model in MODELS_WITH_FALLBACK:
            # "default" lets the API choose the fallback model by refusal category.
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _result(self, request: ChatRequest, message: BetaMessage, started: float) -> ChatResult:
        # Thinking blocks come first and are empty by default; the answer is the text.
        text = "".join(block.text for block in message.content if block.type == "text")
        return ChatResult(
            text=text,
            provider=self.provider,
            # The model that actually answered: a fallback model, if one took over.
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            stop_reason=STOP_REASONS.get(message.stop_reason or "", "other"),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            purpose=request.purpose,
        )


def raise_if_no_credentials(error: TypeError) -> None:
    """Turn the SDK's error for missing credentials into one that says how to fix it.

    The SDK raises a plain TypeError at request time, not one of its APIError classes
    (anthropic 2.11.0; lesson 6.2). It's recognised by its message; any other TypeError
    is a bug and is left alone.
    """
    is_missing_credentials = "Could not resolve authentication method" in str(error)
    if is_missing_credentials:
        raise LLMError(
            "no Claude credentials: set ANTHROPIC_API_KEY, or run `ant auth login`"
        ) from error
