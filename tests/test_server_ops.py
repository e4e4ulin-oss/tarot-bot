"""Операции над службой: какие команды собираются и что считается недоступным."""

from __future__ import annotations

import pytest

from bot.services.server import (
    LOGS_MAX,
    LOGS_MIN,
    NOT_AVAILABLE,
    TIMED_OUT,
    UPDATE_APPLIED,
    CommandResult,
    ServerOps,
    _without_noise,
)


class RecordingOps(ServerOps):
    """Записывает команду вместо запуска: проверяем именно её состав."""

    def __init__(self, result: CommandResult | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.commands: list[tuple[str, ...]] = []
        self.result = result or CommandResult(0, "")

    async def _run(self, *cmd: str) -> CommandResult:
        self.commands.append(cmd)
        return self.result


@pytest.mark.asyncio
async def test_logs_command_names_the_service():
    ops = RecordingOps(service="my-bot")

    await ops.logs(25)

    assert ops.commands == [("journalctl", "-u", "my-bot", "-n", "25", "--no-pager")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asked", "expected"),
    [(1, LOGS_MIN), (0, LOGS_MIN), (-20, LOGS_MIN), (5000, LOGS_MAX)],
)
async def test_log_length_stays_within_bounds(asked: int, expected: int):
    """Число строк приходит из чата, поэтому ограничено с обеих сторон."""
    ops = RecordingOps()

    await ops.logs(asked)

    assert ops.commands[0][4] == str(expected)


@pytest.mark.asyncio
async def test_update_asks_the_script_not_to_restart():
    ops = RecordingOps(update_script="/usr/local/bin/tarot-update")

    await ops.update()

    assert ops.commands == [("sudo", "-n", "/usr/local/bin/tarot-update", "--no-restart")]


@pytest.mark.asyncio
async def test_restart_does_not_wait_for_itself():
    """Без --no-block systemctl ждал бы остановки службы, которая его запустила."""
    ops = RecordingOps(service="my-bot")

    await ops.restart()

    assert ops.commands == [("sudo", "-n", "systemctl", "--no-block", "restart", "my-bot")]


@pytest.mark.asyncio
async def test_output_and_code_come_from_the_process():
    ops = ServerOps()

    result = await ops._run("sh", "-c", "echo готово; exit 10")

    assert result.code == UPDATE_APPLIED
    assert result.output == "готово"
    assert not result.ok
    assert not result.unavailable


@pytest.mark.asyncio
async def test_missing_command_is_unavailable_not_a_crash():
    ops = ServerOps()

    result = await ops._run("/нет/такой/команды")

    assert result.code == NOT_AVAILABLE
    assert result.unavailable


@pytest.mark.asyncio
async def test_hanging_command_is_cut_off():
    ops = ServerOps(timeout=0.2)

    result = await ops._run("sleep", "30")

    assert result.code == TIMED_OUT
    assert result.unavailable
    assert "не ответила" in result.output


def test_sudo_hostname_warning_does_not_reach_the_chat():
    """Предупреждение sudo про /etc/hosts читается как ошибка, а ошибкой не является."""
    output = "sudo: unable to resolve host UK82709: Name or service not known\nуже свежая версия"

    assert _without_noise(output) == "уже свежая версия"


def test_real_errors_survive_the_cleanup():
    assert _without_noise("sudo: a password is required") == "sudo: a password is required"


@pytest.mark.asyncio
async def test_noise_is_stripped_from_a_real_run():
    ops = ServerOps()

    result = await ops._run(
        "sh",
        "-c",
        "echo 'sudo: unable to resolve host UK82709: Name or service not known' >&2; echo готово",
    )

    assert result.output == "готово"
