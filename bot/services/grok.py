"""Клиент xAI Grok (OpenAI-совместимый Chat Completions API)."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class GrokError(RuntimeError):
    """Grok недоступен или вернул некорректный ответ."""


class GrokClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-4",
        timeout: float = 60.0,
        max_tokens: int = 1200,
        temperature: float = 0.8,
        retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        """Один запрос к модели. Бросает GrokError, если ответ получить не удалось."""
        if not self.enabled:
            raise GrokError("XAI_API_KEY не задан")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }

        client = await self._http()
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                response = await client.post("/chat/completions", json=payload)
                if response.status_code >= 500 or response.status_code == 429:
                    raise GrokError(f"Grok ответил {response.status_code}")
                response.raise_for_status()
                data = response.json()
                text = (data["choices"][0]["message"]["content"] or "").strip()
                if not text:
                    raise GrokError("Grok вернул пустой ответ")
                return text
            except (httpx.HTTPError, GrokError, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                logger.warning("Запрос к Grok не удался (попытка %s): %s", attempt + 1, exc)
                if attempt < self.retries:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise GrokError(f"Grok недоступен: {last_error}")
