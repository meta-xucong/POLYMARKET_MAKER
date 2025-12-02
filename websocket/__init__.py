"""Stub of websocket-client for offline testing.

Provides minimal ``WebSocketApp`` placeholder to satisfy imports without
performing any network activity.
"""
from __future__ import annotations
from typing import Any, Callable, Optional


class WebSocketApp:
    def __init__(self, url: str, on_message: Optional[Callable[[Any, Any], None]] = None,
                 on_error: Optional[Callable[[Any, Exception], None]] = None,
                 on_close: Optional[Callable[[Any], None]] = None,
                 on_open: Optional[Callable[[Any], None]] = None):
        self.url = url
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.on_open = on_open

    def run_forever(self, sslopt: Optional[dict] = None, ping_interval: Optional[float] = None,
                    ping_timeout: Optional[float] = None, **kwargs: Any) -> None:
        # Immediately invoke open then close callbacks to simulate a short-lived session.
        if callable(self.on_open):
            self.on_open(self)
        if callable(self.on_close):
            self.on_close(self)


__all__ = ["WebSocketApp"]
