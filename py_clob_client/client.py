"""Offline-friendly stub implementation of ClobClient.

The methods provide deterministic, side-effect-free responses suitable for
local tests that only verify that the dependency is importable and callable.
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .exceptions import PolyApiException


@dataclass
class _ApiCreds:
    key: str
    secret: str
    passphrase: str


class ClobClient:
    def __init__(self, host: str, key: str, chain_id: int, signature_type: int, funder: str):
        self.host = host
        self.key = key
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder = funder
        self._api_creds: Optional[_ApiCreds] = None

    # Public endpoints
    def get_ok(self) -> Dict[str, str]:
        return {"status": "ok"}

    def get_server_time(self) -> Dict[str, float]:
        return {"serverTime": time.time()}

    # Auth helpers
    def create_or_derive_api_creds(self) -> _ApiCreds:
        # Generate deterministic placeholder credentials.
        return _ApiCreds(key="stub-key", secret="stub-secret", passphrase="stub-pass")

    def set_api_creds(self, creds: _ApiCreds) -> None:
        if not isinstance(creds, _ApiCreds):
            raise PolyApiException("Invalid credentials object")
        self._api_creds = creds

    # Private endpoints
    def get_orders(self, params: Any) -> Dict[str, Any]:
        if self._api_creds is None:
            raise PolyApiException("API credentials not set")
        return {"market": getattr(params, "market", None), "orders": []}


__all__ = ["ClobClient"]
