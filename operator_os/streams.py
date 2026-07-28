"""Editable digit→stream URL map (digits 3/4 by default). YAML on disk."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
STREAMS_PATH = ROOT / "data" / "streams.yaml"

_UA = "operator-os/1.0"

# Built-in defaults — used when file missing; also written on first save.
DEFAULT_STREAMS: dict[str, dict[str, str]] = {
    "3": {
        "label": "WAMU 88.5",
        "url": "https://static.wamu.org/streams/live/1/mp3.1.pls",
    },
    "4": {
        "label": "NWS weather radio",
        "url": "https://stream.mikev.com/khb36.mp3",
    },
}


def streams_path() -> Path:
    return STREAMS_PATH


def load_streams() -> dict[str, dict[str, str]]:
    """Return digit(str) → {label, url}. Missing file → defaults (not written)."""
    path = STREAMS_PATH
    if not path.is_file():
        return {k: dict(v) for k, v in DEFAULT_STREAMS.items()}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {k: dict(v) for k, v in DEFAULT_STREAMS.items()}
    items = raw.get("streams", raw) if isinstance(raw, dict) else {}
    out: dict[str, dict[str, str]] = {}
    if not isinstance(items, dict):
        return {k: dict(v) for k, v in DEFAULT_STREAMS.items()}
    for key, val in items.items():
        dig = str(key).strip()
        if dig not in ("3", "4"):
            continue
        if not isinstance(val, dict):
            continue
        url = str(val.get("url") or "").strip()
        label = str(val.get("label") or "").strip() or f"Stream {dig}"
        if not url:
            continue
        out[dig] = {"label": label, "url": url}
    for k, v in DEFAULT_STREAMS.items():
        out.setdefault(k, dict(v))
    return out


def probe_stream_url(url: str, *, timeout: float = 8.0) -> str:
    """Resolve playlist if needed, then confirm the media URL responds.

    Returns the playable media URL. Raises ValueError on failure so the
    console can refuse a bad save before the phone dials it.
    """
    from operator_os.audio import resolve_stream_url

    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    try:
        media = resolve_stream_url(u, timeout=timeout)
    except Exception as e:
        raise ValueError(f"playlist/resolve failed: {e}") from e
    if not media.startswith(("http://", "https://")):
        raise ValueError("resolved media is not http(s)")

    headers = {
        "User-Agent": _UA,
        "Range": "bytes=0-1023",
    }
    req = urllib.request.Request(media, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            resp.read(64)
    except urllib.error.HTTPError as e:
        if e.code in (200, 206):
            return media
        raise ValueError(f"HTTP {e.code} for media URL") from e
    except Exception as e:
        raise ValueError(f"unreachable media: {e}") from e
    if int(code) >= 400:
        raise ValueError(f"HTTP {code} for media URL")
    return media


def save_streams(
    streams: dict[str, Any],
    *,
    probe: bool = True,
    timeout: float = 8.0,
) -> dict[str, dict[str, str]]:
    """Validate (and optionally probe) then write. Only digits 3 and 4."""
    out: dict[str, dict[str, str]] = {}
    for dig in ("3", "4"):
        src = streams.get(dig) or streams.get(int(dig)) or {}
        if not isinstance(src, dict):
            src = {}
        url = str(src.get("url") or "").strip()
        label = str(src.get("label") or "").strip() or DEFAULT_STREAMS[dig]["label"]
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"digit {dig}: url must be http(s)")
        if probe:
            try:
                probe_stream_url(url, timeout=timeout)
            except ValueError as e:
                raise ValueError(f"digit {dig}: {e}") from e
        out[dig] = {"label": label, "url": url}
    path = STREAMS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({"streams": out}, default_flow_style=False, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    return out


def get_stream(digit: int) -> tuple[str, str] | None:
    """(label, url) for a stream digit, or None."""
    entry = load_streams().get(str(digit))
    if not entry:
        return None
    return entry["label"], entry["url"]
