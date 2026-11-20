"""Choosing a provider from the settings, and recording what each call cost (lesson 6.2)."""

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from atlas.config import Settings
from atlas.llm import ChatProvider, ChatResult, EmbeddingProvider
from atlas.models import LlmCall

logger = logging.getLogger("atlas.llm")

DEFAULT_MODELS = {"ollama": "qwen3:1.7b", "anthropic": "claude-opus-5-5"}

# Dollars per million input and output tokens, from Anthropic's price list (November 2026).
# Local models cost electricity, not money: they're priced at zero.
PRICES_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}


def make_chat(settings: Settings) -> ChatProvider:
    model = settings.chat_model or DEFAULT_MODELS[settings.llm_provider]
    if settings.llm_provider == "anthropic":
        # Imported here so Atlas runs without the anthropic package's import cost
        # (and without a key) when it only uses local models.
        from atlas.llm.claude import ClaudeChat

        return ClaudeChat(model=model, effort=settings.llm_effort)
    from atlas.llm.ollama import OllamaChat

    return OllamaChat(model=model, base_url=settings.ollama_url)


def make_embeddings(settings: Settings) -> EmbeddingProvider:
    from atlas.llm.ollama import OllamaEmbeddings

    return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.ollama_url)


def cost_usd(result: ChatResult) -> Decimal | None:
    if result.provider == "ollama":
        return Decimal(0)
    prices = PRICES_PER_MILLION.get(result.model)
    if prices is None:
        logger.warning("no price for model %s; its cost is not recorded", result.model)
        return None
    input_price, output_price = prices
    total = result.input_tokens * input_price + result.output_tokens * output_price
    return (total / 1_000_000).quantize(Decimal("0.000001"))


def record(session: Session, result: ChatResult) -> LlmCall:
    """Add the call to the cost log, in the caller's transaction."""
    call = LlmCall(
        provider=result.provider,
        model=result.model,
        purpose=result.purpose,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost_usd(result),
        latency_ms=result.latency_ms,
        stop_reason=result.stop_reason,
    )
    session.add(call)
    return call
