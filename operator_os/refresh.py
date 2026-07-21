"""Fetch and cache local news/weather. Stdlib HTTP only."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile

DATA = Path("data")
WEATHER_JSON = DATA / "weather.json"
WEATHER_WAV = DATA / "weather.wav"
NEWS_JSON = DATA / "news.json"
NEWS_WAV = DATA / "news.wav"
NEWS_MP3 = DATA / "news.mp3"

# Fairfax, Virginia area (fallback if profile lat/lon unset).
_DEFAULT_LAT = 38.8462
_DEFAULT_LON = -77.3064

_WMO: dict[int, str] = {
    0: "clear skies",
    1: "mainly clear skies",
    2: "partly cloudy skies",
    3: "overcast skies",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    80: "light rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorms",
    96: "thunderstorms with slight hail",
    99: "thunderstorms with heavy hail",
}


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader — no dependency. Does not override existing env."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def refresh_all(profile: HardwareProfile, *, weather: bool = True, news: bool = True) -> int:
    """Refresh selected caches. Returns process exit code."""
    load_dotenv()
    DATA.mkdir(parents=True, exist_ok=True)
    rc = 0
    if weather:
        try:
            path = refresh_weather(profile)
            print(f"weather ok: {path}")
        except Exception as e:
            print(f"weather failed: {e}", flush=True)
            rc = 1
    if news:
        try:
            paths = refresh_news(profile)
            print(f"news ok: {paths['json']}  audio={paths.get('audio')}")
        except Exception as e:
            print(f"news failed: {e}", flush=True)
            rc = 1
    return rc


def refresh_weather(profile: HardwareProfile, *, synthesize: bool = True) -> Path:
    providers = profile.raw.get("providers", {})
    weather_cfg = providers.get("weather", {})
    lat = weather_cfg.get("latitude")
    lon = weather_cfg.get("longitude")
    if lat is None or lon is None:
        lat, lon = _DEFAULT_LAT, _DEFAULT_LON
    label = str(weather_cfg.get("location_label") or "Fairfax, Virginia area")
    endpoint = str(
        weather_cfg.get("endpoint") or "https://api.open-meteo.com/v1/forecast"
    )

    qs = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "America/New_York",
        }
    )
    url = f"{endpoint}?{qs}"
    raw = _http_json(url)
    spoken = format_weather_spoken(raw, location_label=label)
    payload = {
        "fetched_at": _utcnow(),
        "provider": "open-meteo",
        "location_label": label,
        "latitude": float(lat),
        "longitude": float(lon),
        "spoken": spoken,
        "current": raw.get("current", {}),
    }
    WEATHER_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if synthesize:
        audio = AudioRouter(profile.audio)
        try:
            out = audio.synthesize(spoken, output_path=WEATHER_WAV)
            if out is None:
                print("weather: json ok, TTS wav failed (digit 2 will speak text)", flush=True)
            else:
                print(f"weather audio: {WEATHER_WAV}", flush=True)
        finally:
            audio.close()
    return WEATHER_JSON


def refresh_news(profile: HardwareProfile, *, synthesize: bool = True) -> dict[str, Path]:
    api_key = os.environ.get("NEWSDATA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "NEWSDATA_API_KEY not set (add to .env). Weather refresh does not need a key."
        )

    qs = urllib.parse.urlencode(
        {
            "apikey": api_key,
            "country": "us",
            "language": "en",
            "category": "top",
        }
    )
    url = f"https://newsdata.io/api/1/latest?{qs}"
    raw = _http_json(url)
    results = raw.get("results") or []
    headlines = []
    for item in results:
        title = (item.get("title") or "").strip()
        if title:
            headlines.append(title)
        if len(headlines) >= 8:
            break
    if not headlines:
        raise RuntimeError("newsdata.io returned no headlines")

    spoken = format_news_spoken(headlines)
    payload = {
        "fetched_at": _utcnow(),
        "provider": "newsdata.io",
        "spoken": spoken,
        "headlines": headlines,
    }
    NEWS_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    out: dict[str, Path] = {"json": NEWS_JSON}
    if synthesize:
        audio = AudioRouter(profile.audio)
        try:
            wav = audio.synthesize(spoken, output_path=NEWS_WAV)
            if wav is None:
                raise RuntimeError("TTS failed while rendering news.wav")
            out["audio"] = NEWS_WAV
        finally:
            audio.close()
    return out


def format_weather_spoken(payload: dict[str, Any], *, location_label: str) -> str:
    cur = payload.get("current") or {}
    temp = cur.get("temperature_2m")
    humidity = cur.get("relative_humidity_2m")
    wind = cur.get("wind_speed_10m")
    code = cur.get("weather_code")
    cond = _WMO.get(int(code), "unusual conditions") if code is not None else "conditions unavailable"
    parts = [f"Weather Bureau report for the {location_label}."]
    if temp is not None:
        parts.append(f"The temperature is {round(float(temp))} degrees Fahrenheit, with {cond}.")
    else:
        parts.append(f"Currently {cond}.")
    if humidity is not None:
        parts.append(f"Relative humidity {round(float(humidity))} percent.")
    if wind is not None:
        parts.append(f"Winds around {round(float(wind))} miles per hour.")
    return " ".join(parts)


def format_news_spoken(headlines: list[str]) -> str:
    lines = [
        "News of the Day.",
        "Here are tonight's principal headlines.",
    ]
    for i, title in enumerate(headlines, start=1):
        lines.append(f"{i}. {title}.")
    lines.append("That is the news.")
    return " ".join(lines)


def _http_json(url: str, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "we302-operator/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {e.url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"network error: {e.reason}") from e
    data = json.loads(body)
    if not isinstance(data, dict):
        raise RuntimeError("expected JSON object")
    return data


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
