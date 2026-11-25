"""Provider-configurable chat LLM with a two-method interface: stream() and complete().

groq / openai use the openai SDK (Groq exposes an OpenAI-compatible endpoint);
anthropic uses the anthropic SDK.
"""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Protocol

from api.core.config import get_settings

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",  # Groq retired llama-3.3-70b-versatile; override with LLM_MODEL
    "openai": "gpt-4o-mini",
    "anthropic": "claude-opus-5-5",
}

Message = dict[str, str]  # {"role": "user" | "assistant", "content": str}


class LLMNotConfigured(RuntimeError):
    pass


class LLM(Protocol):
    def stream(self, system: str, messages: list[Message], max_tokens: int = 2048) -> AsyncIterator[str]: ...
    async def complete(self, system: str, messages: list[Message], max_tokens: int = 512) -> str: ...


class OpenAICompatibleLLM:
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def stream(self, system: str, messages: list[Message], max_tokens: int = 2048) -> AsyncIterator[str]:
        resp = await self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens, temperature=0.1, stream=True,
            messages=[{"role": "system", "content": system}, *messages],
        )
        async for event in resp:
            if event.choices and event.choices[0].delta.content:
                yield event.choices[0].delta.content

    async def complete(self, system: str, messages: list[Message], max_tokens: int = 512) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens, temperature=0.0,
            messages=[{"role": "system", "content": system}, *messages],
        )
        return resp.choices[0].message.content or ""


class AnthropicLLM:
    # Server-side fallback: if the model declines, the API re-runs the request on a fallback model.
    _FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    def __init__(self, api_key: str, model: str):
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key or None)
        self.model = model

    async def stream(self, system: str, messages: list[Message], max_tokens: int = 2048) -> AsyncIterator[str]:
        async with self.client.beta.messages.stream(
            model=self.model, max_tokens=max_tokens, system=system, messages=messages,
            output_config={"effort": "low"}, **self._FALLBACK,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def complete(self, system: str, messages: list[Message], max_tokens: int = 512) -> str:
        async with self.client.beta.messages.stream(
            model=self.model, max_tokens=max_tokens, system=system, messages=messages,
            output_config={"effort": "low"}, **self._FALLBACK,
        ) as stream:
            final = await stream.get_final_message()
        return "".join(b.text for b in final.content if b.type == "text")


@lru_cache
def get_llm() -> LLM:
    s = get_settings()
    model = s.llm_model or DEFAULT_MODELS.get(s.llm_provider, "")
    if s.llm_provider == "groq" and s.groq_api_key:
        return OpenAICompatibleLLM(s.groq_api_key, model, base_url="https://api.groq.com/openai/v1")
    if s.llm_provider == "openai" and s.openai_api_key:
        return OpenAICompatibleLLM(s.openai_api_key, model)
    if s.llm_provider == "anthropic":
        return AnthropicLLM(s.anthropic_api_key, model)
    raise LLMNotConfigured(
        f"LLM provider '{s.llm_provider}' has no API key. Set GROQ_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY."
    )


def llm_available() -> bool:
    try:
        get_llm()
        return True
    except LLMNotConfigured:
        return False
