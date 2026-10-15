"""Клиент xAI Grok (OpenAI-совместимый Chat Completions API)."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Сколько текста ответа показывать: объяснение провайдера короткое,
# а за ним обычно идёт служебный мусор
EXPLANATION_LIMIT = 300


def _explain(response: "httpx.Response") -> str:
    """Человеческая часть отказа провайдера."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:EXPLANATION_LIMIT].strip() or "без объяснения"

    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            return str(error.get("message") or error)[:EXPLANATION_LIMIT]
        return str(error)[:EXPLANATION_LIMIT]
    return str(data)[:EXPLANATION_LIMIT]


class GrokError(RuntimeError):
    """Grok недоступен или вернул некорректный ответ."""


class Refused(GrokError):
    """Провайдер отказал осознанно: повторять такой запрос бессмысленно.

    Такое бывает, когда модели с таким именем нет, параметр не поддержан или
    кончился баланс. Объяснение приходит в теле ответа, и оно единственное,
    что здесь по-настоящему полезно.
    """

    def __init__(self, status: int, explanation: str) -> None:
        super().__init__(f"{status}: {explanation}")
        self.status = status
        self.explanation = explanation


def extract_text(data: dict) -> str:
    """Достаёт ответ модели, объясняя пустой результат вместо глухого «пусто».

    Рассуждающие модели тратят часть лимита на внутренние размышления: если лимит мал,
    видимый текст оказывается пустым, а причина остановки — length.
    """
    try:
        choice = data["choices"][0]
        text = (choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GrokError(f"неожиданный формат ответа: {str(data)[:200]}") from exc

    if text:
        return text
    if choice.get("finish_reason") == "length":
        raise GrokError(
            "ответ не поместился в лимит токенов — модель потратила его на размышления; "
            "увеличьте GROK_MAX_TOKENS"
        )
    raise GrokError("модель вернула пустой ответ")


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
                if response.status_code >= 400:
                    # Провайдер объясняет отказ в теле ответа: имя модели,
                    # неподдержанный параметр, кончившийся баланс. Прятать это
                    # за «400 Bad Request» — значит гадать вместо чтения
                    raise Refused(response.status_code, _explain(response))
                response.raise_for_status()
                return extract_text(response.json())
            except Refused:
                raise
            except (httpx.HTTPError, GrokError, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                logger.warning("Запрос к Grok не удался (попытка %s): %s", attempt + 1, exc)
                if attempt < self.retries:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise GrokError(f"Grok недоступен: {last_error}")


    async def models(self) -> list[str]:
        """Какие модели провайдер готов дать этому ключу.

        При отказе вида «модели нет» это единственный ответ, который сразу
        говорит, что писать в настройках.
        """
        if not self.enabled:
            raise GrokError("ключ не задан")

        client = await self._http()
        response = await client.get("/models")
        if response.status_code >= 400:
            raise Refused(response.status_code, _explain(response))

        data = response.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        names = []
        for item in items or []:
            name = item.get("id") if isinstance(item, dict) else str(item)
            if name:
                names.append(str(name))
        return sorted(names)
