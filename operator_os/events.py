"""JSONL + in-memory event log for debugging and phase acceptance."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EventLog:
    path: Path = Path("data/events.jsonl")
    maxlen: int = 200
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))

    def __post_init__(self) -> None:
        self.recent = deque(maxlen=self.maxlen)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: str, **fields: Any) -> dict[str, Any]:
        event = {"type": type, "ts": _utcnow(), **fields}
        self.recent.append(event)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
        return event


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
