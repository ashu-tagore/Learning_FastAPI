"""Fake model servers for tests: the real HTTP clients, answered in memory (lesson 6.2).

They return canned text, so they test Atlas's handling of a model's answers (parsing,
retries, validation, logging), never the quality of the answers themselves. Quality is
measured against real models, in lesson 6.8's evaluations.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import httpx2


class FakeOllama:
    """Answers /api/chat like Ollama, with the replies a test queues."""

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.requests: list[dict[str, Any]] = []
        # Status codes to answer with before the next reply, to exercise retries.
        self.failures: list[int] = []
        self.done_reason = "stop"

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.failures:
            return httpx.Response(self.failures.pop(0), json={"error": "model is loading"})
        text = self.replies.pop(0)
        counts = {"prompt_eval_count": 42, "eval_count": len(text.split())}
        final = {"done": True, "done_reason": self.done_reason, **counts}
        if not body["stream"]:
            message = {"role": "assistant", "content": text}
            return httpx.Response(200, json={"message": message, **final})
        lines = [
            json.dumps({"message": {"role": "assistant", "content": word}, "done": False})
            for word in text.split(" ")
        ]
        lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, **final}))
        return httpx.Response(200, content="\n".join(lines).encode())

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def claude_message(
    text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5"
) -> dict[str, Any]:
    """A Messages API response body, in the shape the API documents."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }


def claude_transport(
    respond: Callable[[dict[str, Any], httpx2.Request], httpx2.Response],
) -> httpx2.MockTransport:
    """A transport for the Anthropic SDK, which uses httpx2 (not httpx) since its 1.0."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        return respond(json.loads(request.content), request)

    return httpx2.MockTransport(handle)
