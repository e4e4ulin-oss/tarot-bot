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
