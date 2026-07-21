"""Local digit services. Deterministic; no cloud required."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceResult:
    digit: int
    kind: str  # speak | play_file | effect_then_speak | outside_seize
    text: str
    path: Path | None = None


NEWS_AUDIO_CANDIDATES = (Path("data/news.wav"), Path("data/news.mp3"))
NEWS_JSON = Path("data/news.json")
WEATHER_CACHE = Path("data/weather.json")


def handle_digit(digit: int) -> ServiceResult:
    if digit == 0:
        return ServiceResult(
            digit=0,
            kind="speak",
            text=(
                "Operator. Local services: dial 1 for news, 2 for weather. "
                "Outside line seizes a trunk tone; cloud operator is not yet available."
            ),
        )
    if digit == 1:
        audio = _news_audio()
        if audio is not None:
            return ServiceResult(digit=1, kind="play_file", text="", path=audio)
        spoken = _news_spoken_fallback()
        if spoken:
            return ServiceResult(digit=1, kind="speak", text=spoken)
        return ServiceResult(
            digit=1,
            kind="speak",
            text="News of the Day is not yet on file. Please try again later.",
        )
    if digit == 2:
        weather = _weather_summary()
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


def _news_audio() -> Path | None:
    for path in NEWS_AUDIO_CANDIDATES:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _news_spoken_fallback() -> str | None:
    if not NEWS_JSON.is_file():
        return None
    try:
        data = json.loads(NEWS_JSON.read_text(encoding="utf-8"))
        return str(data.get("spoken") or "").strip() or None
    except (OSError, ValueError):
        return None


def _weather_summary() -> str | None:
    if not WEATHER_CACHE.is_file():
        return None
    try:
        data = json.loads(WEATHER_CACHE.read_text(encoding="utf-8"))
        return str(data.get("spoken") or data.get("summary") or "").strip() or None
    except (OSError, ValueError):
        return None
