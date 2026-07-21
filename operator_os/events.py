"""JSONL + in-memory event log for debugging and phase acceptance."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Soft cap on on-disk JSONL size; trimmed to the last maxlen lines when exceeded.
_MAX_FILE_BYTES = 256_000


@dataclass
class EventLog:
    path: Path = Path("data/events.jsonl")
    maxlen: int = 200
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))

    def __post_init__(self) -> None:
        self.recent = deque(maxlen=self.maxlen)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._trim_file_if_needed()

    def emit(self, type: str, **fields: Any) -> dict[str, Any]:
        # Never log secrets or full private recording paths contents.
        safe = {k: v for k, v in fields.items() if k not in ("api_key", "password", "token")}
        event = {"type": type, "ts": _utcnow(), **safe}
        self.recent.append(event)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
        self._trim_file_if_needed()
        return event

    def _trim_file_if_needed(self) -> None:
        try:
            if not self.path.is_file() or self.path.stat().st_size <= _MAX_FILE_BYTES:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()
            keep = lines[-self.maxlen :]
            self.path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        except OSError:
            pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
