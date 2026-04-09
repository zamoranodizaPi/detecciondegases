from __future__ import annotations

import secrets
import threading
import time
from typing import Optional

from config import ConfigManager


class TokenStore:
    def __init__(self, config_manager: ConfigManager, ttl_seconds: int = 8 * 3600) -> None:
        self.config_manager = config_manager
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def login(self, username: str, password: str) -> Optional[str]:
        if not self.config_manager.authenticate(username, password):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = time.time() + self.ttl_seconds
        return token

    def verify(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expires_at = self._tokens.get(token)
            if expires_at is None or expires_at < time.time():
                self._tokens.pop(token, None)
                return False
            return True
