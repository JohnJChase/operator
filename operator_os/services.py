"""Local digit services. Deterministic; no cloud required."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceResult:
    digit: int
    kind: str  # speak | play_file | effect_then_speak | outside_seize | operator
    text: str
    path: Path | None = None


NEWS_AUDIO_CANDIDATES = (Path("data/news.wav"), Path("data/news.mp3"))
NEWS_JSON = Path("data/news.json")
WEATHER_AUDIO = Path("data/weather.wav")
WEATHER_CACHE = Path("data/weather.json")


def handle_digit(digit: int) -> ServiceResult:
    if digit == 0:
        return ServiceResult(
            digit=0,
            kind="operator",
            text="",
        )
    if digit == 1:
        audio = _first_existing(NEWS_AUDIO_CANDIDATES)
        if audio is not None:
            return ServiceResult(digit=1, kind="play_file", text="", path=audio)
        spoken = _json_spoken(NEWS_JSON)
        if spoken:
            return ServiceResult(digit=1, kind="speak", text=spoken)
        return ServiceResult(
            digit=1,
            kind="speak",
            text="News of the Day is not yet on file. Please try again later.",
        )
    if digit == 2:
        if WEATHER_AUDIO.is_file() and WEATHER_AUDIO.stat().st_size > 0:
            return ServiceResult(digit=2, kind="play_file", text="", path=WEATHER_AUDIO)
        weather = _json_spoken(WEATHER_CACHE)
        if weather:
            return ServiceResult(digit=2, kind="speak", text=weather)
        return ServiceResult(
            digit=2,
            kind="speak",
            text="Weather Bureau report is not yet on file. Please try again later.",
        )
    if digit == 9:
        return ServiceResult(
            digit=9,
            kind="outside_seize",
            text="Outside line.",
        )
    if 3 <= digit <= 8:
        return ServiceResult(
            digit=digit,
            kind="speak",
            text="That service is not yet available.",
        )
    return ServiceResult(digit=digit, kind="speak", text="Invalid selection.")


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _json_spoken(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("spoken") or data.get("summary") or "").strip() or None
    except (OSError, ValueError):
        return None
