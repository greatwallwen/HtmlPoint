"""Ephemeral launch handshake for the loopback helper."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode


class SessionRejected(ValueError):
    """A launch nonce or session credential was invalid."""


class BrowserLaunchError(RuntimeError):
    """The configured application could not be opened without exposing its URL."""


class LaunchSession:
    """Hold one short-lived launch nonce and a separate in-memory token."""

    __slots__ = (
        "allowed_origin",
        "_exchange_lock",
        "_expires_at",
        "_launch_nonce",
        "_monotonic",
        "_session_token",
        "_used",
    )

    def __init__(
        self,
        *,
        allowed_origin: str,
        launch_nonce: str,
        session_token: str,
        expires_at: float,
        monotonic: Callable[[], float],
    ) -> None:
        self.allowed_origin = allowed_origin
        self._exchange_lock = threading.Lock()
        self._launch_nonce = launch_nonce
        self._session_token = session_token
        self._expires_at = expires_at
        self._monotonic = monotonic
        self._used = False

    @classmethod
    def create(
        cls,
        *,
        allowed_origin: str,
        ttl_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> LaunchSession:
        if not allowed_origin:
            raise ValueError("allowed origin must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("launch session TTL must be positive")
        launch_nonce = secrets.token_urlsafe(32)
        session_token = secrets.token_urlsafe(32)
        while secrets.compare_digest(launch_nonce, session_token):
            session_token = secrets.token_urlsafe(32)
        if len(launch_nonce) < 43 or len(session_token) < 43:
            raise RuntimeError("secure launch material was unexpectedly short")
        return cls(
            allowed_origin=allowed_origin,
            launch_nonce=launch_nonce,
            session_token=session_token,
            expires_at=monotonic() + ttl_seconds,
            monotonic=monotonic,
        )

    @property
    def launch_nonce(self) -> str:
        return self._launch_nonce

    def exchange(self, nonce: str, *, origin: str) -> str:
        with self._exchange_lock:
            if origin != self.allowed_origin:
                raise SessionRejected("launch session rejected")
            if self._used or self._monotonic() >= self._expires_at:
                raise SessionRejected("launch session rejected")
            if not secrets.compare_digest(nonce, self._launch_nonce):
                raise SessionRejected("launch session rejected")
            self._used = True
            return self._session_token

    def verify_token(self, candidate: str) -> bool:
        return bool(candidate) and secrets.compare_digest(candidate, self._session_token)

    def issue_same_origin_token(self, *, origin: str) -> str:
        """Issue a session token for a same-origin request (no nonce needed).

        Safe because the helper only listens on 127.0.0.1 and the web app
        is served from the same origin. A cross-origin request will have a
        different Origin header and be rejected.
        """
        with self._exchange_lock:
            if origin != self.allowed_origin and origin != "":
                raise SessionRejected("launch session rejected")
            return self._session_token

    def connect_url(self, *, web_application_url: str, helper_base_url: str) -> str:
        fragment = urlencode(
            {
                "helper": helper_base_url,
                "nonce": self._launch_nonce,
            }
        )
        return f"{web_application_url.rstrip('/')}/#{fragment}"

    def open_browser(
        self,
        *,
        web_application_url: str,
        helper_base_url: str,
        opener: Callable[[str], bool],
    ) -> None:
        connect_url = self.connect_url(
            web_application_url=web_application_url,
            helper_base_url=helper_base_url,
        )
        try:
            opened = opener(connect_url)
        except Exception:
            raise BrowserLaunchError("browser launch failed") from None
        if not opened:
            raise BrowserLaunchError("browser launch failed")

    def __repr__(self) -> str:
        return f"LaunchSession(allowed_origin={self.allowed_origin!r}, secrets=<redacted>)"
