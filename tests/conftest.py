import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Значения по умолчанию, чтобы Settings собирался в тестах без реального .env
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "-100123")
os.environ.setdefault("ADMIN_IDS", "1,2")
