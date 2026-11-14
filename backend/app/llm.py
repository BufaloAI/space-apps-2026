"""vLLM'e (OpenAI uyumlu API) bağlanan ince istemci katmanı."""
from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .config import Settings


class LLM:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client = AsyncOpenAI(
            base_url=settings.vllm_base_url,
            api_key=settings.vllm_api_key,
            timeout=60.0,
            max_retries=1,
        )

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Cevabı parça parça (token grupları halinde) döndürür."""
        response = await self._client.chat.completions.create(
            model=self._s.vllm_model,
            messages=messages,
            stream=True,
            max_tokens=self._s.max_tokens,
            temperature=self._s.temperature,
            # Qwen3 varsayılan olarak "düşünme modu"nda gelir; sohbet için kapalı tutuyoruz.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        async for chunk in response:
            if chunk.choices:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

    async def close(self) -> None:
        await self._client.close()
