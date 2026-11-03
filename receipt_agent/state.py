"""Remembers which emails were already checked, so each one is read by Claude only once."""

from __future__ import annotations

import json
from pathlib import Path


class SeenStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.seen: set[str] = set()
        if self.path.exists():
            self.seen = set(json.loads(self.path.read_text()))

    def __contains__(self, key: str) -> bool:
        return key in self.seen

    def add(self, key: str) -> None:
        self.seen.add(key)

    def save(self) -> None:
        self.path.write_text(json.dumps(sorted(self.seen), indent=0))
