"""Load hardware profile YAML. Pins live here, not in code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROFILE = Path("config/hardware_profile.yaml")


@dataclass(frozen=True)
class GpioPins:
    hook_bcm: int
    dial_pulse_bcm: int
    ring_bcm: int


@dataclass(frozen=True)
class DialTiming:
    digit_done_ms: int
    pulse_debounce_ms: int


@dataclass(frozen=True)
class HookTiming:
    debounce_ms: int
    flash_min_ms: int
    flash_max_ms: int
    hangup_min_ms: int


@dataclass(frozen=True)
class RingTiming:
    cadence_on_ms: int
    cadence_off_ms: int
    poll_hook_while_ringing_ms: int
    max_ring_on_ms: int


@dataclass(frozen=True)
class AudioConfig:
    alsa_device: str
    sample_rate_hz: int
    channels: int
    format: str
    piper_voice: str
    piper_volume: float = 0.6


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    gpio: GpioPins
    dial: DialTiming
    hook: HookTiming
    ring: RingTiming
    audio: AudioConfig
    raw: dict[str, Any]


def load_profile(path: Path | str | None = None) -> HardwareProfile:
    profile_path = Path(path) if path else DEFAULT_PROFILE
    with profile_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    gpio = data["gpio"]
    dial = gpio["dial_pulse"]
    hook = gpio["hook"]
    ring = gpio["ring"]
    audio = data["audio"]
    tts = audio.get("tts", {})
    timing = data.get("timing", {})
    cadence = ring.get("cadence", {})

    return HardwareProfile(
        name=data["profile"]["name"],
        gpio=GpioPins(
            hook_bcm=int(hook["bcm"]),
            dial_pulse_bcm=int(dial["bcm"]),
            ring_bcm=int(ring["bcm"]),
        ),
        dial=DialTiming(
            digit_done_ms=int(dial.get("digit_done_ms", 700)),
            pulse_debounce_ms=int(dial.get("debounce_ms", 20)),
        ),
        hook=HookTiming(
            debounce_ms=int(hook.get("debounce_ms", 50)),
            flash_min_ms=int(timing.get("hook_flash_min_ms", 100)),
            flash_max_ms=int(timing.get("hook_flash_max_ms", 700)),
            hangup_min_ms=int(timing.get("definite_hangup_min_ms", 1000)),
        ),
        ring=RingTiming(
            cadence_on_ms=int(cadence.get("on_ms", 2000)),
            cadence_off_ms=int(cadence.get("off_ms", 4000)),
            poll_hook_while_ringing_ms=int(ring.get("hook_poll_ms_while_ringing", 50)),
            max_ring_on_ms=int(ring.get("max_ring_on_ms", 2000)),
        ),
        audio=AudioConfig(
            alsa_device=str(audio.get("device", "plughw:2,0")),
            sample_rate_hz=int(audio.get("sample_rate_hz", 16000)),
            channels=int(audio.get("channels", 1)),
            format=str(audio.get("format", "S16_LE")),
            piper_voice=str(tts.get("piper_voice", "hfc_female")),
            piper_volume=float(tts.get("piper_volume", 0.6)),
        ),
        raw=data,
    )
