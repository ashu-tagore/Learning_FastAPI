"""Fake model servers for tests: the real HTTP clients, answered in memory (lesson 6.2).

They return canned text, so they test Atlas's handling of a model's answers (parsing,
retries, validation, logging), never the quality of the answers themselves. Quality is
measured against real models, in lesson 6.8's evaluations.
"""

import hashlib
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
        # Word by word, each after the first carrying its leading space, as Ollama sends them.
        words = text.split(" ")
        pieces = words[:1] + [" " + word for word in words[1:]]
        lines = [
            json.dumps({"message": {"role": "assistant", "content": piece}, "done": False})
            for piece in pieces
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


class FakeEmbeddings:
    """Deterministic stand-in for an embedding model: texts sharing words get similar vectors.

    Each word adds 1 to one of 768 positions chosen by its hash, then the vector is scaled
    to length 1. It has no idea what words mean, which is the point: tests check Atlas's
    plumbing (batching, storage, ranking arithmetic), not a model's understanding.
    """

    model = "fake-embed"
    dimensions = 768

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []  # (batch size, purpose)

    def embed(self, texts: list[str], purpose: str) -> list[list[float]]:
        self.calls.append((len(texts), purpose))
        return [self.vector(text) for text in texts]

    def vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        for word in text.lower().replace(".", " ").replace("(", " ").split():
            digest = hashlib.sha256(word.encode()).digest()
            values[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        length = sum(v * v for v in values) ** 0.5 or 1.0
        return [v / length for v in values]
