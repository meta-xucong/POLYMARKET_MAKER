"""Lightweight offline stub of py_clob_client.

This module provides minimal classes and helpers used by local quickstart
checks when the official dependency cannot be installed (e.g., due to network
restrictions). The API surface mirrors the pieces consumed by
``polymarket_clob_quickstart_test.py`` and is intentionally small.
"""

from .client import ClobClient
from .constants import POLYGON
from .clob_types import OpenOrderParams
from .exceptions import PolyApiException

__all__ = ["ClobClient", "POLYGON", "OpenOrderParams", "PolyApiException"]
