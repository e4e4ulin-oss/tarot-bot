"""Проверка deploy/autoupdate.sh на настоящем git-репозитории.

Скрипт — единственное, что доставляет правки с телефона на сервер, поэтому он
проверяется целиком: с подменёнными systemctl и pip, но с настоящими fetch,
reset и сравнением ревизий.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "autoupdate.sh"


def git(*args: str, cwd: Path) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def commit(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-qm", f"add {name}", cwd=repo)
    git("push", "-q", "origin", "HEAD:main", cwd=repo)


@pytest.fixture
def server(tmp_path: Path) -> dict:
    """Серверный каталог бота, его origin и заглушки системных команд."""
    origin = tmp_path / "origin.git"
    # -b main: иначе HEAD пустого origin указывает на несуществующий master
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)

    author = tmp_path / "author"  # то, из чего правят код: ПК или телефон
    git("clone", "-q", f"file://{origin}", str(author), cwd=tmp_path)
    (author / "bot").mkdir()
    commit(author, "bot/__init__.py", "ВЕРСИЯ = 1\n")
    (author / "requirements.txt").write_text("aiogram\n", encoding="utf-8")
    git("add", "requirements.txt", cwd=author)
    git("commit", "-qm", "deps", cwd=author)
    git("push", "-q", "origin", "HEAD:main", cwd=author)

    deployed = tmp_path / "opt" / "tarot-bot"
    deployed.parent.mkdir()
    git("clone", "-q", "--depth", "1", f"file://{origin}", str(deployed), cwd=tmp_path)

    # Заглушки: записывают свои аргументы, ничего не делая с системой.
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    calls = tmp_path / "calls.log"
    stub = stub_bin / "systemctl"
    stub.write_text(f'#!/bin/sh\necho "systemctl $*" >> "{calls}"\n', encoding="utf-8")
    stub.chmod(0o755)

    venv_bin = deployed / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    pip = venv_bin / "pip"
    pip.write_text(f'#!/bin/sh\necho "pip $*" >> "{calls}"\n', encoding="utf-8")
    pip.chmod(0o755)
    # python настоящий: проверку компиляции хочется видеть как она есть
    python = venv_bin / "python"
    python.write_text('#!/bin/sh\nexec python3 "$@"\n', encoding="utf-8")
    python.chmod(0o755)

    home = tmp_path / "home"  # чтобы safe.directory не лез в настоящий ~/.gitconfig
    home.mkdir()

    return {"author": author, "deployed": deployed, "bin": stub_bin, "calls": calls, "home": home}


def run_update(server: dict, *flags: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{server['bin']}:{os.environ['PATH']}",
        "HOME": str(server["home"]),
        "TAROT_SERVICE": "test-bot",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), str(server["deployed"]), *flags],
        env=env,
        capture_output=True,
        text=True,
    )


def calls(server: dict) -> str:
    log = server["calls"]
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_up_to_date_changes_nothing(server: dict) -> None:
    done = run_update(server)

    assert done.returncode == 0, done.stderr
    assert "уже свежая версия" in done.stdout
    assert "systemctl" not in calls(server)


def test_new_commit_arrives_and_bot_restarts(server: dict) -> None:
    commit(server["author"], "feature.py", "правка с телефона\n")

    done = run_update(server)

    assert done.returncode == 0, done.stderr
    assert (server["deployed"] / "feature.py").read_text(encoding="utf-8") == "правка с телефона\n"
    assert "systemctl restart test-bot" in calls(server)
    assert "перезапущен" in done.stdout


def test_dependencies_installed_only_when_their_list_changes(server: dict) -> None:
    commit(server["author"], "feature.py", "правка без новых зависимостей\n")
    run_update(server)
    assert "pip install" not in calls(server)

    (server["author"] / "requirements.txt").write_text("aiogram\nhttpx\n", encoding="utf-8")
    git("add", "requirements.txt", cwd=server["author"])
    git("commit", "-qm", "new dep", cwd=server["author"])
    git("push", "-q", "origin", "HEAD:main", cwd=server["author"])

    done = run_update(server)

    assert done.returncode == 0, done.stderr
    assert "pip install" in calls(server)
    assert "доустанавливаю" in done.stdout


def test_missing_repository_is_reported(tmp_path: Path, server: dict) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {
        **os.environ,
        "PATH": f"{server['bin']}:{os.environ['PATH']}",
        "HOME": str(server["home"]),
    }

    done = subprocess.run(
        ["bash", str(SCRIPT), str(empty)], env=env, capture_output=True, text=True
    )

    assert done.returncode == 1
    assert "нет репозитория" in done.stdout
    assert "systemctl" not in calls(server)


def test_broken_code_is_rolled_back_without_restarting(server: dict) -> None:
    before = git("rev-parse", "HEAD", cwd=server["deployed"])
    commit(server["author"], "bot/broken.py", "def  # недописанная функция\n")

    done = run_update(server)

    assert done.returncode == 1
    assert "откатываюсь" in done.stdout
    assert git("rev-parse", "HEAD", cwd=server["deployed"]) == before
    assert not (server["deployed"] / "bot" / "broken.py").exists()
    assert "systemctl" not in calls(server)


def test_no_restart_leaves_the_service_alone(server: dict) -> None:
    """Так обновление вызывает сам бот: он должен успеть ответить в Telegram."""
    commit(server["author"], "feature.py", "правка из чата\n")

    done = run_update(server, "--no-restart")

    assert done.returncode == 10, done.stderr  # UPDATE_APPLIED
    assert (server["deployed"] / "feature.py").exists()
    assert "systemctl" not in calls(server)
    assert "перезапуск за вызывающим" in done.stdout


def test_no_restart_on_fresh_code_reports_nothing_to_do(server: dict) -> None:
    done = run_update(server, "--no-restart")

    assert done.returncode == 0, done.stderr
    assert "уже свежая версия" in done.stdout
    assert "systemctl" not in calls(server)


def test_unknown_flag_is_refused(server: dict) -> None:
    done = run_update(server, "--surprise")

    assert done.returncode == 2
    assert "неизвестный параметр" in done.stderr
