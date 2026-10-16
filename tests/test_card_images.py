"""Картинки карт: перевёрнутая карта показывается перевёрнутой картинкой."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from bot.tarot import get_card
from bot.tarot import images as images_module
from bot.tarot.images import (
    OWN_SUFFIX,
    REVERSED_SUFFIX,
    cache_key,
    reversed_image_bytes,
    rotate_180,
)

CARD = get_card("major_18")

# Общая заглушка из conftest подменяет загрузку картинок, а здесь проверяется
# именно она — забираем настоящую функцию до подмены и возвращаем на место.
REAL_CARD_IMAGE_BYTES = images_module.card_image_bytes


@pytest.fixture(autouse=True)
def _restore_real_loader(monkeypatch):
    monkeypatch.setattr(images_module, "card_image_bytes", REAL_CARD_IMAGE_BYTES)


def sample_image(width: int = 64, height: int = 32) -> bytes:
    """Белая картинка с красным верхним левым углом."""
    image = Image.new("RGB", (width, height), (255, 255, 255))
    for x in range(width // 2):
        for y in range(height // 2):
            image.putpixel((x, y), (255, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_rotation_moves_the_corner_across_the_diagonal():
    turned = Image.open(BytesIO(rotate_180(sample_image())))

    assert turned.size == (64, 32)
    assert turned.getpixel((5, 5))[0] > 200 and turned.getpixel((5, 5))[1] > 200  # стал белым
    assert turned.getpixel((58, 27))[0] > 200 and turned.getpixel((58, 27))[1] < 50  # стал красным


def test_rotating_twice_returns_the_original_orientation():
    once = rotate_180(sample_image())
    twice = Image.open(BytesIO(rotate_180(once)))

    assert twice.getpixel((5, 5))[1] < 50  # красный снова наверху слева


def test_cache_key_separates_the_two_orientations(monkeypatch):
    monkeypatch.setattr("bot.tarot.images.local_path", lambda card: None)

    assert cache_key(CARD, False) == "major_18"
    assert cache_key(CARD, True) == f"major_18{REVERSED_SUFFIX}"
    assert len(cache_key(CARD, True)) <= 32  # столько отведено в таблице card_images


def test_our_own_card_gets_its_own_cache_key(monkeypatch, tmp_path):
    """Нарисованная карта не должна застрять в чате сканом Уэйта.

    File_id под ключом «major_18» уже сохранён и показывает скан. Если бы
    ключ не менялся, своя картинка не попала бы в чат никогда.
    """
    monkeypatch.setattr("bot.tarot.images.local_path", lambda card: tmp_path / "major_18.jpg")

    assert cache_key(CARD, False) == f"major_18{OWN_SUFFIX}"
    assert cache_key(CARD, True) == f"major_18{OWN_SUFFIX}{REVERSED_SUFFIX}"
    assert len(cache_key(CARD, True)) <= 32


@pytest.mark.asyncio
async def test_local_file_is_used_and_rotated(monkeypatch, tmp_path):
    source = tmp_path / "major_18.png"
    source.write_bytes(sample_image())
    monkeypatch.setattr("bot.tarot.images.local_path", lambda card: source)

    turned = Image.open(BytesIO(await reversed_image_bytes(CARD)))

    assert turned.getpixel((58, 27))[1] < 50


@pytest.mark.asyncio
async def test_unreachable_source_gives_none_instead_of_raising(monkeypatch):
    """Картинка — украшение: недоступный источник не должен ломать расклад."""
    monkeypatch.setattr("bot.tarot.images.local_path", lambda card: None)

    async def refuse(self, url):
        raise __import__("httpx").ConnectError("нет сети")

    monkeypatch.setattr("httpx.AsyncClient.get", refuse)

    assert await reversed_image_bytes(CARD) is None


@pytest.mark.asyncio
async def test_broken_file_gives_none(monkeypatch, tmp_path):
    source = tmp_path / "major_18.png"
    source.write_bytes(b"not an image at all")
    monkeypatch.setattr("bot.tarot.images.local_path", lambda card: source)

    assert await reversed_image_bytes(CARD) is None
