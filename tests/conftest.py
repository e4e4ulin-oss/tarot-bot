import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Значения по умолчанию, чтобы Settings собирался в тестах без реального .env
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "-100123")
os.environ.setdefault("ADMIN_IDS", "1,2")


import pytest

from bot.config import Settings


@pytest.fixture(autouse=True)
def _ignore_local_env_file(monkeypatch):
    """Тесты не должны зависеть от .env, случайно лежащего в рабочем каталоге."""
    original = Settings.__init__

    def init(self, **values):
        values.setdefault("_env_file", None)
        original(self, **values)

    monkeypatch.setattr(Settings, "__init__", init)


def _tiny_png() -> bytes:
    """Картинка-заглушка: белый квадрат с красным углом, чтобы поворот был заметен."""
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (32, 32), (255, 255, 255))
    for x in range(16):
        for y in range(16):
            image.putpixel((x, y), (255, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


SAMPLE_CARD_IMAGE = _tiny_png()


@pytest.fixture(autouse=True)
def _card_images_without_network(monkeypatch):
    """Перевёрнутые карты поворачиваются по-настоящему, но без похода на Викисклад."""

    async def stored(card):
        return SAMPLE_CARD_IMAGE

    monkeypatch.setattr("bot.tarot.images.card_image_bytes", stored)
