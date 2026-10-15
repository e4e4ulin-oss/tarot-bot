"""Сторож запуска: падение на старте должно доходить до владельца.

Упавший на старте бот — единственный случай, когда ни одна команда не
работает: спросить логи не у кого. Поэтому о падении он обязан сообщить сам.
"""

import json
from unittest.mock import patch

import pytest

from bot.__main__ import launch, tell_the_owner


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
