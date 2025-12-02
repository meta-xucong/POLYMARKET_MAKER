"""Minimal offline stub of the ``requests`` package.

This stub supplies the small surface area needed for tests that import
``Volatility_arbitrage_run`` without performing real HTTP calls. Functions raise
an error by default to make unintended network usage visible in tests.
"""
from __future__ import annotations
from typing import Any, Dict, Optional


class RequestException(Exception):
    pass


class Timeout(RequestException):
    pass


class _Response:
    def __init__(self, status_code: int = 200, json_data: Optional[Dict[str, Any]] = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self) -> Dict[str, Any]:
        return dict(self._json_data)


# Default functions raise to avoid silent external calls.
def get(url: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> _Response:
    raise Timeout("requests.get stub invoked without patching")


def post(url: str, data: Any = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> _Response:
    raise Timeout("requests.post stub invoked without patching")


__all__ = ["RequestException", "Timeout", "get", "post"]
