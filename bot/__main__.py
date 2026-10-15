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
import traceback
import urllib.request

TELEGRAM = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 3000  # в сообщение Telegram влезает 4096 знаков, оставляем запас


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

    tail = "".join(traceback.format_exception(error))[-LIMIT:]
    body = json.dumps(
        {
            "chat_id": chat,
            "text": f"Бот не поднялся:\n\n<pre>{tail}</pre>",
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
        if not isinstance(error, (KeyboardInterrupt, SystemExit)):
            tell_the_owner(error)
        raise


if __name__ == "__main__":
    sys.exit(launch())
