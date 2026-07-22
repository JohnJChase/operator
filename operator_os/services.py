"""Local digit services. Deterministic; no cloud required."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# WAMU 88.5 HD-1 — live NPR from American University Radio.
WAMU_PLS = "https://static.wamu.org/streams/live/1/mp3.1.pls"
# NOAA Weather Radio KHB36 (Washington / Baltimore area) via mikev.com relay.
NWS_KHB36 = "https://stream.mikev.com/khb36.mp3"

LOCAL_MENU = (
    "Operator. Dial 1 for news, 2 for weather, "
    "3 for WAMU, 4 for weather radio, "
    "5 for voicemail, "
    "7 to join a meeting, "
    "8 for the information desk, 9 for outside line. "
    "Dial 0 to hear this again."
)


@dataclass(frozen=True)
class ServiceResult:
    digit: int
    kind: str  # speak | play_file | stream | outside_seize | info_desk | …
    text: str
    path: Path | None = None
    url: str | None = None


NEWS_AUDIO_CANDIDATES = (Path("data/news.wav"), Path("data/news.mp3"))
NEWS_JSON = Path("data/news.json")
WEATHER_AUDIO = Path("data/weather.wav")
WEATHER_CACHE = Path("data/weather.json")


def handle_digit(digit: int) -> ServiceResult:
    if digit == 0:
        text = LOCAL_MENU
        try:
            from operator_os.db import unheard_count, unheard_voicemail_count

            n = unheard_count()
            nv = unheard_voicemail_count()
        except Exception:
            n = 0
            nv = 0
        if n == 1:
            text = f"{text} You have 1 unheard message."
        elif n > 1:
            text = f"{text} You have {n} unheard messages."
        if nv == 1:
            text = f"{text} You have 1 new voicemail."
        elif nv > 1:
            text = f"{text} You have {nv} new voicemails."
        return ServiceResult(digit=0, kind="speak", text=text)
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
    if digit == 3:
        return ServiceResult(
            digit=3,
            kind="stream",
            text="WAMU 88.5",
            url=WAMU_PLS,
        )
    if digit == 4:
        return ServiceResult(
            digit=4,
            kind="stream",
            text="National Weather Service radio",
            url=NWS_KHB36,
        )
    if digit == 5:
        return ServiceResult(digit=5, kind="mailbox", text="Voicemail.")
    if digit == 7:
        return ServiceResult(digit=7, kind="join_meeting", text="Join meeting.")
    if digit == 8:
        return ServiceResult(digit=8, kind="info_desk", text="")
    if digit == 9:
        return ServiceResult(
            digit=9,
            kind="outside_seize",
            text="Outside line.",
        )
    if digit == 6:
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
