"""API мини-приложения: тот же бот, но с экранами вместо кнопок в чате."""

from __future__ import annotations

from .app import create_app, serve
from .auth import AuthError, Identity, WebAppUser, verify

__all__ = [
    "AuthError",
    "Identity",
    "WebAppUser",
    "create_app",
    "serve",
    "verify",
]
