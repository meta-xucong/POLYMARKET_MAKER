"""Type objects mirroring the handful used in quickstart checks."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OpenOrderParams:
    """Lightweight representation of the order query parameters."""

    market: Optional[str] = None


__all__ = ["OpenOrderParams"]
