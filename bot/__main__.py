"""Запуск бота: `python -m bot`.

Здесь намеренно пусто. Настоящий запуск живёт в bot/run.py, а этот файл —
только сторож: он ловит всё, что падает при импорте и на старте, и успевает
рассказать об этом в Telegram, прежде чем процесс умрёт.

Нужно это потому, что упавший на старте бот — единственный случай, когда
никто не может ни спросить у него логи, ни получить ответ: команды работают
только у живого процесса. Один вечер ушёл ровно на это: сервер перезапускал
бота девяносто два раза, а причину пришлось доставать руками с машины.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path

TELEGRAM = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 3000  # в сообщение Telegram влезает 4096 знаков, оставляем запас

# Служба перезапускается, пока не поднимется, — значит об одной и той же
# причине она узнаёт раз в полминуты. Рассказывать об этом каждый раз нельзя:
# чат, в котором одно сообщение в минуту, перестаёт читаться совсем. Поэтому
# о причине говорим раз в час, а остальное считаем — в журнале всё равно всё.
QUIET = 3600
STATE = "last-crash.json"


def _state_file() -> Path:
    """Где помнить, о чём уже рассказали.

    Рядом с базой: этот каталог точно доступен службе на запись.
    """
    folder = os.environ.get("TAROT_STATE_DIR")
    if not folder:
        url = os.environ.get("DATABASE_URL", "")
        marker = "sqlite+aiosqlite:///"
        folder = str(Path(url[len(marker) :]).parent) if url.startswith(marker) else "data"
    return Path(folder) / STATE


def unreported(signature: str, now: float | None = None) -> int:
    """Сколько падений накопилось, если о причине пора рассказать, иначе ноль.

    Память — файл: сторож живёт ровно одну попытку запуска и между попытками
    не помнит ничего. Если памяти нет (каталог недоступен), рассказываем —
    молчать о падении хуже, чем повториться.
    """
    now = time.time() if now is None else now
    path = _state_file()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - первый запуск, битый файл, нет прав
        state = {}

    same = state.get("signature") == signature
    # Череда считается по одной причине: падения прошлой к новой не относятся
    count = int(state.get("silent", 0)) + 1 if same else 1
    told = float(state.get("told", 0))
    quiet = same and now - told < QUIET

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "signature": signature,
                    "told": told if quiet else now,
                    "silent": count if quiet else 0,
                }
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - без памяти сторож просто разговорчивее
        pass

    return 0 if quiet else count


def tell_the_owner(error: BaseException) -> None:
    """Пробует донести причину падения до админ-чата.

    Берёт токен и чат прямо из окружения: к этому моменту ни настройки, ни
    база могут быть недоступны. Любая ошибка здесь проглатывается — сторож
    не имеет права мешать настоящей ошибке попасть в журнал.
    """
    token = os.environ.get("BOT_TOKEN", "")
    chat = os.environ.get("ADMIN_CHAT_ID", "")
    if not token or not chat:
        return

    signature = traceback.format_exception_only(type(error), error)[-1].strip()
    count = unreported(signature)
    if not count:
        return  # об этой причине рассказали меньше часа назад

    tail = "".join(traceback.format_exception(error))[-LIMIT:]
    again = (
        f"\n\nС прошлого сообщения он упал ещё {count - 1} раз — причина та же."
        if count > 1
        else "\n\nПока причина не изменится, я не повторяюсь: журнал полнее — "
        "journalctl -u tarot-bot -n 50"
    )
    body = json.dumps(
        {
            "chat_id": chat,
            "text": f"Бот не поднялся:\n\n<pre>{tail}</pre>{again}",
            "parse_mode": "HTML",
        }
    ).encode()

    try:
        request = urllib.request.Request(
            TELEGRAM.format(token=token),
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=10).close()
    except Exception:  # noqa: BLE001 - сторож молчит о собственных бедах
        pass


def launch() -> int:
    try:
        from .run import run

        return run()
    except BaseException as error:  # noqa: BLE001 - сообщаем и отдаём дальше
        if not isinstance(error, KeyboardInterrupt | SystemExit):
            tell_the_owner(error)
        raise


if __name__ == "__main__":
    sys.exit(launch())
