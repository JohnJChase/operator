"""Local digit services. Deterministic; no cloud required."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceResult:
    digit: int
    kind: str  # speak | play_file | effect_then_speak
    text: str
    path: Path | None = None


NEWS_CACHE = Path("data/news.mp3")
WEATHER_CACHE = Path("data/weather.json")


def handle_digit(digit: int) -> ServiceResult:
    if digit == 0:
        return ServiceResult(
            digit=0,
            kind="speak",
            text=(
                "Operator. Local services: dial 1 for news, 2 for weather. "
                "Outside line and cloud operator are not yet available."
            ),
        )
    if digit == 1:
        if NEWS_CACHE.is_file():
            return ServiceResult(digit=1, kind="play_file", text="", path=NEWS_CACHE)
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


def _weather_summary() -> str | None:
    if not WEATHER_CACHE.is_file():
        return None
    try:
        import json

        data = json.loads(WEATHER_CACHE.read_text(encoding="utf-8"))
        return str(data.get("spoken") or data.get("summary") or "").strip() or None
    except (OSError, ValueError):
        return None
