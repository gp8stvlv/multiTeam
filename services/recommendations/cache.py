"""Bounded, process-local cache of validated meal selections."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Callable


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass
class CachedSelection:
    constraints: dict
    plan: dict


class RecommendationCache:
    def __init__(self, *, capacity: int = 128, ttl_seconds: float = 600,
                 clock: Callable[[], float] = time.monotonic):
        if capacity < 1 or ttl_seconds <= 0:
            raise ValueError("Cache capacity and lifetime must be positive")
        self.capacity, self.ttl_seconds, self.clock = capacity, ttl_seconds, clock
        self._entries: OrderedDict[str, tuple[float, CachedSelection]] = OrderedDict()

    def get(self, key: str) -> CachedSelection | None:
        stored = self._entries.get(key)
        if stored is None:
            return None
        expires, selection = stored
        if self.clock() >= expires:
            self.discard(key)
            return None
        self._entries.move_to_end(key)
        return deepcopy(selection)

    def put(self, key: str, selection: CachedSelection) -> None:
        now = self.clock()
        for expired in [k for k, (expires, _) in self._entries.items() if expires <= now]:
            self.discard(expired)
        self._entries[key] = (now + self.ttl_seconds, deepcopy(selection))
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)

    def discard(self, key: str) -> None:
        self._entries.pop(key, None)
