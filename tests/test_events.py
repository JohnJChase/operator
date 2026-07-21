"""Event log bounding."""

from __future__ import annotations

from pathlib import Path

from operator_os.events import EventLog, _MAX_FILE_BYTES


def test_event_log_trims_when_oversized(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    # Pre-fill past the soft cap.
    blob = '{"type":"pad","ts":"x"}\n' * ((_MAX_FILE_BYTES // 20) + 50)
    path.write_text(blob, encoding="utf-8")
    assert path.stat().st_size > _MAX_FILE_BYTES

    log = EventLog(path=path, maxlen=20)
    log.emit("hook", value="off_hook")
    assert path.stat().st_size <= _MAX_FILE_BYTES
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) <= 21  # trim then one emit
    assert '"type":"hook"' in lines[-1]
