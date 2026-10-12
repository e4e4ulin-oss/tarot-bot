"""Приводит фон карты к цвету своей группы, не трогая фигуру.

Модель постоянно затемняет фон: просишь кармин — получаешь бордо. Перерисовывать
из-за этого целую карту расточительно, а фон у нас плоский, и его можно перекрасить
точно.

Работает это только там, где фигура отличается по цвету от фона. Если ткань написана
тем же красным, что и фон, границы между ними нет, заливка проходит сквозь платье, и
карту надо перерисовывать, а не чинить. Скрипт это замечает сам и отказывается.

    python scripts/fix_ground.py КАРТА.png "#8F0303" [РЕЗУЛЬТАТ.png]
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter

# Насколько пиксель может отличаться от фона и всё ещё считаться фоном
TOLERANCE = 26


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def ground_mask(image: Image.Image, tolerance: int = TOLERANCE) -> Image.Image:
    """Маска фона: заливка от краёв по похожему цвету."""
    width, height = image.size
    px = image.load()
    seed = px[2, 2]

    mask = Image.new("L", (width, height), 0)
    mp = mask.load()
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        queue.extend([(x, 0), (x, height - 1)])
    for y in range(height):
        queue.extend([(0, y), (width - 1, y)])

    while queue:
        x, y = queue.popleft()
        if not (0 <= x < width and 0 <= y < height) or mp[x, y]:
            continue
        r, g, b = px[x, y]
        if abs(r - seed[0]) + abs(g - seed[1]) + abs(b - seed[2]) > tolerance * 3:
            continue
        mp[x, y] = 255
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # Мягкий край, чтобы по контуру фигуры не было ступеньки
    return mask.filter(ImageFilter.GaussianBlur(1.2))


def recolour(image: Image.Image, target: tuple[int, int, int]) -> Image.Image:
    """Фон в заданный цвет с сохранением фактуры и виньетки."""
    mask = ground_mask(image)
    grey = image.convert("L")

    values = [v for v, m in zip(grey.getdata(), mask.getdata(), strict=True) if m > 200]
    mean = max(1.0, sum(values) / max(1, len(values)))

    painted = Image.new("RGB", image.size)
    painted.putdata(
        [
            tuple(min(255, max(0, round(c * value / mean))) for c in target)
            for value in grey.getdata()
        ]
    )
    return Image.composite(painted, image, mask)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2

    source = Path(argv[1])
    target = hex_to_rgb(argv[2])
    out = Path(argv[3]) if len(argv) > 3 else source

    image = Image.open(source).convert("RGB")
    mask = ground_mask(image)
    share = sum(1 for v in mask.getdata() if v > 200) / (image.width * image.height)
    if share < 0.15:
        print(f"фон занял всего {share:.0%} кадра — заливка не нашла его, проверьте карту")
        return 1
    if share > 0.75:
        print(
            f"заливка захватила {share:.0%} кадра — фигура написана в цвет фона, "
            "границы между ними нет. Такую карту надо перерисовать, а не перекрашивать."
        )
        return 1

    recolour(image, target).save(out)
    shade = "#{:02X}{:02X}{:02X}".format(*target)
    print(f"фон ({share:.0%} кадра) перекрашен в {shade} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
