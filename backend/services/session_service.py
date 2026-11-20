"""Sesiones de un solo proceso: reiniciar FastAPI invalida todas las cookies emitidas.

No compartir esta instancia entre workers. Solo se guardan digests SHA-256 y los
identificadores de cuenta; los tokens opacos existen únicamente en las cookies.
"""

import hashlib
import re
import secrets
import time
from collections.abc import Callable
from threading import Lock

SESSION_COOKIE = "decibelcity_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


class SessionStore:
    def __init__(
        self, *, ttl_seconds: float = SESSION_TTL_SECONDS, max_sessions: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_sessions < 1:
            raise ValueError("La duración y el límite de sesiones deben ser positivos.")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._lock = Lock()
        # dict conserva el orden de creación para expulsar la sesión más antigua.
        self._sessions: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _digest(token: str | None) -> str | None:
        if token is None or _TOKEN_PATTERN.fullmatch(token) is None:
            return None
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def create(self, id_usuario: int, previous_token: str | None = None) -> str:
        """Emite un token nuevo y revoca la cookie anterior en una sola sección crítica."""
        with self._lock:
            now = self._clock()
            self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}
            self._sessions.pop(self._digest(previous_token), None)
            while len(self._sessions) >= self._max_sessions:
                del self._sessions[next(iter(self._sessions))]
            token = secrets.token_urlsafe(32)
            digest = self._digest(token)
            while digest in self._sessions:
                token = secrets.token_urlsafe(32)
                digest = self._digest(token)
            self._sessions[digest] = (id_usuario, now + self._ttl_seconds)
            return token

    def resolve(self, token: str | None) -> int | None:
        digest = self._digest(token)
        if digest is None:
            return None
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return None
            if session[1] <= self._clock():
                del self._sessions[digest]
                return None
            return session[0]

    def revoke(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(self._digest(token), None)
