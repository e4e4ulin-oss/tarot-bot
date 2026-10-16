"""Сторож запуска: падение на старте должно доходить до владельца.

Упавший на старте бот — единственный случай, когда ни одна команда не
работает: спросить логи не у кого. Поэтому о падении он обязан сообщить сам.
"""

import json
from unittest.mock import patch

import pytest

from bot.__main__ import QUIET, launch, tell_the_owner, unreported


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Память сторожа — у каждого теста своя, иначе они слышат друг друга."""
    monkeypatch.setenv("TAROT_STATE_DIR", str(tmp_path))


class FakeResponse:
    def close(self) -> None:
        pass


def test_startup_failure_reaches_the_owner(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_CHAT_ID", "555")

    sent = {}

    def capture(request, timeout=0):
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data)
        return FakeResponse()

    with patch("urllib.request.urlopen", capture):
        with patch("bot.run.run", side_effect=NameError("name 'ensure_default' is not defined")):
            with pytest.raises(NameError):
                launch()

    assert "123:abc" in sent["url"]
    assert sent["body"]["chat_id"] == "555"
    assert "ensure_default" in sent["body"]["text"]


def test_watchdog_keeps_quiet_without_a_chat(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.delenv("ADMIN_CHAT_ID", raising=False)

    with patch("urllib.request.urlopen", side_effect=AssertionError("не должен звонить")):
        tell_the_owner(RuntimeError("тишина"))


def test_watchdog_never_hides_the_real_error(monkeypatch):
    """Своя беда сторожа не должна подменять настоящую ошибку."""
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_CHAT_ID", "555")

    with patch("urllib.request.urlopen", side_effect=OSError("сеть лежит")):
        tell_the_owner(RuntimeError("настоящая причина"))


def test_stopping_by_hand_is_not_a_crash(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_CHAT_ID", "555")

    with patch("urllib.request.urlopen", side_effect=AssertionError("не должен звонить")):
        with patch("bot.run.run", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                launch()


def test_the_same_cause_is_told_once_an_hour():
    """Служба перезапускается без конца, и сообщение об этом — тоже спам."""
    assert unreported("OperationalError: no such column", now=0) == 1
    assert unreported("OperationalError: no such column", now=60) == 0
    assert unreported("OperationalError: no such column", now=120) == 0

    # Через час о причине напоминаем и говорим, сколько падений промолчали
    assert unreported("OperationalError: no such column", now=QUIET + 1) == 3


def test_a_new_cause_is_told_at_once():
    """Новая причина — новый счёт: молчание о прошлой к ней не относится."""
    assert unreported("первая причина", now=0) == 1
    assert unreported("первая причина", now=10) == 0
    assert unreported("вторая причина", now=20) == 1


def test_the_repeated_crash_does_not_reach_the_chat(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_CHAT_ID", "555")

    calls = []

    def capture(request, timeout=0):
        calls.append(json.loads(request.data))
        return FakeResponse()

    error = RuntimeError("одна и та же беда")
    with patch("urllib.request.urlopen", capture):
        for _ in range(5):
            tell_the_owner(error)

    assert len(calls) == 1
    assert "не повторяюсь" in calls[0]["text"]


def test_a_watchdog_without_memory_still_speaks(monkeypatch):
    """Если каталог недоступен, сторож разговорчив — это лучше, чем молчалив."""
    monkeypatch.setenv("TAROT_STATE_DIR", "/proc/нельзя-писать")
    assert unreported("беда", now=0) == 1
    assert unreported("беда", now=1) == 1
