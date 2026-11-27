"""O único componente que fala com o provider. Cada chamada gera um span com as GenAI semantic conventions."""

import json
from urllib.parse import urlparse

import httpx
from opentelemetry.trace import SpanKind

from . import config
from .telemetry import tracer

_client = httpx.Client(
    base_url=config.PROVIDER_BASE_URL,
    headers={"Authorization": f"Bearer {config.PROVIDER_API_KEY}"},
    timeout=20.0,
)
_SERVER = urlparse(config.PROVIDER_BASE_URL)


def _common(span, operation: str, model: str) -> None:
    span.set_attribute("gen_ai.operation.name", operation)
    span.set_attribute("gen_ai.provider.name", "openai")
    span.set_attribute("gen_ai.request.model", model)
    span.set_attribute("server.address", _SERVER.hostname or "")
    span.set_attribute("server.port", _SERVER.port or 0)


def chat(messages: list[dict]) -> tuple[str, dict]:
    """Devolve o texto gerado e o objeto usage do provider."""
    with tracer.start_as_current_span(f"chat {config.CHAT_MODEL}", kind=SpanKind.CLIENT) as span:
        _common(span, "chat", config.CHAT_MODEL)
        if config.CAPTURE_CONTENT:
            # Só em development (ADR 0005); o Collector ainda redige CPF e salário daqui.
            span.set_attribute("gen_ai.input.messages", json.dumps(
                [{"role": m["role"], "parts": [{"type": "text", "content": m["content"]}]} for m in messages],
                ensure_ascii=False))
        response = _client.post("/chat/completions", json={"model": config.CHAT_MODEL, "messages": messages})
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage", {})
        text = body["choices"][0]["message"]["content"]
        span.set_attribute("gen_ai.response.model", body.get("model", ""))
        span.set_attribute("gen_ai.response.id", body.get("id", ""))
        span.set_attribute("gen_ai.response.finish_reasons", [body["choices"][0].get("finish_reason") or ""])
        span.set_attribute("gen_ai.usage.input_tokens", usage.get("prompt_tokens", 0))
        span.set_attribute("gen_ai.usage.output_tokens", usage.get("completion_tokens", 0))
        span.set_attribute("gen_ai.usage.cache_read.input_tokens",
                           (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        if config.CAPTURE_CONTENT:
            span.set_attribute("gen_ai.output.messages", json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": text}]}], ensure_ascii=False))
        return text, usage


def embed(texts: list[str]) -> list[list[float]]:
    with tracer.start_as_current_span(f"embeddings {config.EMBEDDING_MODEL}", kind=SpanKind.CLIENT) as span:
        _common(span, "embeddings", config.EMBEDDING_MODEL)
        response = _client.post("/embeddings", json={"model": config.EMBEDDING_MODEL, "input": texts})
        response.raise_for_status()
        body = response.json()
        span.set_attribute("gen_ai.usage.input_tokens", body.get("usage", {}).get("prompt_tokens", 0))
        data = sorted(body["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]
