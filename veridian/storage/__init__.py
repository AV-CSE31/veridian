"""veridian.storage — Pluggable task storage backends."""

from veridian.storage.base import BaseStorage
from veridian.storage.local_json import LocalJSONStorage

__all__ = [
    "BaseStorage",
    "LocalJSONStorage",
]
