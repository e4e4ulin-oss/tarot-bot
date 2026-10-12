"""Управление службой бота из Telegram: обновление, перезапуск, журнал.

Бот работает под служебным пользователем, у которого нет прав на systemd.
Установщик разрешает ему через sudoers ровно три команды с фиксированными
аргументами, поэтому здесь нет и не должно появляться команд, собранных из
пользовательского ввода: единственное, что приходит извне, — число строк
журнала, и оно ограничено.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# tarot-update возвращает этот код, когда правки применены, а перезапуск оставлен
# вызывающему: так бот успевает ответить до того, как его остановят.
UPDATE_APPLIED = 10

# Код для случаев, когда команду вообще не удалось запустить.
NOT_AVAILABLE = 127
TIMED_OUT = 124

LOGS_DEFAULT = 40
LOGS_MIN = 5
LOGS_MAX = 200


@dataclass(frozen=True)
class CommandResult:
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def unavailable(self) -> bool:
        """Команды нет, прав нет или она не ответила — чинится на сервере."""
        return self.code in (NOT_AVAILABLE, TIMED_OUT)


class ServerOps:
    """Операции над службой бота. Любая может быть недоступна — это нормально."""

    def __init__(
        self,
        service: str = "tarot-bot",
        update_script: str = "/usr/local/bin/tarot-update",
        *,
        timeout: float = 180.0,
    ) -> None:
        self.service = service
        self.update_script = update_script
        self.timeout = timeout

    async def _run(self, *cmd: str) -> CommandResult:
        logger.info("выполняю: %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:  # нет sudo, нет journalctl, нет самого скрипта
            return CommandResult(NOT_AVAILABLE, str(exc))

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(TIMED_OUT, f"команда не ответила за {self.timeout:.0f} с")

        code = proc.returncode if proc.returncode is not None else 0
        return CommandResult(code, stdout.decode("utf-8", "replace").strip())

    async def logs(self, lines: int = LOGS_DEFAULT) -> CommandResult:
        count = max(LOGS_MIN, min(LOGS_MAX, lines))
        return await self._run("journalctl", "-u", self.service, "-n", str(count), "--no-pager")

    async def update(self) -> CommandResult:
        """Подтянуть код из GitHub, не перезапуская: перезапуск — отдельным шагом."""
        return await self._run("sudo", "-n", self.update_script, "--no-restart")

    async def restart(self) -> CommandResult:
        # --no-block: иначе systemctl ждёт завершения службы, которая его же и запустила
        return await self._run("sudo", "-n", "systemctl", "--no-block", "restart", self.service)
