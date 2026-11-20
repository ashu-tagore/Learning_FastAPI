"""Local models through Ollama's HTTP API (https://github.com/ollama/ollama/blob/main/docs/api.md).

Free and private: nothing leaves the machine. Slow on a CPU, and a small model makes
mistakes a large one wouldn't, which Part 6's lessons show rather than hide.
"""

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from atlas.llm import ChatEvent, ChatRequest, ChatResult, LLMError, StopReason, TextDelta

# Ollama's done_reason values, in Atlas's words.
STOP_REASONS: dict[str, StopReason] = {"stop": "end", "length": "max_tokens"}

# Transient failures worth another try: the server restarting, or busy loading a model.
RETRYABLE_STATUS = {500, 502, 503, 504}
ATTEMPTS = 3


class OllamaChat:
    provider = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        # A long timeout: a small CPU can take minutes to read a long prompt.
        self.client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def complete(self, request: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        response = self._post(self._body(request, stream=False))
        data = response.json()
        return self._result(request, data, data["message"]["content"], started)

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        started = time.perf_counter()
        parts: list[str] = []
        with self.client.stream(
            "POST", "/api/chat", json=self._body(request, stream=True)
        ) as response:
            if response.status_code != 200:
                response.read()
                raise LLMError(f"Ollama answered {response.status_code}: {response.text[:200]}")
            # One JSON object per line; the last one has done: true and the counts.
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                piece = data["message"]["content"]
                if piece:
                    parts.append(piece)
                    yield TextDelta(piece)
                if data["done"]:
                    yield self._result(request, data, "".join(parts), started)

    def _body(self, request: ChatRequest, stream: bool) -> dict[str, Any]:
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            # Qwen3 can "think" before answering; on a CPU that multiplies the wait for
            # little gain on Atlas's short tasks, so it's off.
            "think": False,
            "options": {"num_predict": request.max_tokens},
        }
        if request.output_schema is not None:
            # Ollama constrains generation to this JSON schema (structured outputs).
            body["format"] = request.output_schema.model_json_schema()
        return body

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(1, ATTEMPTS + 1):
            is_last = attempt == ATTEMPTS
            try:
                response = self.client.post("/api/chat", json=body)
            except httpx.TransportError as error:
                if is_last:
                    raise LLMError(f"cannot reach Ollama: {error}") from error
                time.sleep(attempt)
                continue
            should_retry = response.status_code in RETRYABLE_STATUS and not is_last
            if should_retry:
                time.sleep(attempt)
                continue
            if response.status_code != 200:
                raise LLMError(f"Ollama answered {response.status_code}: {response.text[:200]}")
            return response
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _result(
        self, request: ChatRequest, data: dict[str, Any], text: str, started: float
    ) -> ChatResult:
        return ChatResult(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            stop_reason=STOP_REASONS.get(data.get("done_reason", ""), "other"),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            purpose=request.purpose,
        )
