"""Off-hook handset bridge: virtual SIP line (snd-aloop) ↔ USB cradle.

Architecture:
  Softphone opens Loopback. Live SIP_CALL patch starts alsaloop (one-way legs
  as a pair today). Plant cordboard owns when the bridge is up — see plant.py.
  ATR2x electrically loops speaker→mic; HandsetSipGuard lowers capture when
  both legs are live (no Meet-specific mute timers).
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


def ensure_loopback_card() -> str:
    """Load snd-aloop if needed; return ALSA card id (name), e.g. ``Loopback``."""
    name = _find_loopback_card()
    if name:
        return name
    try:
        subprocess.run(
            ["sudo", "-n", "modprobe", "snd-aloop", "enable=1,1", "index=10"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    time.sleep(0.3)
    name = _find_loopback_card()
    if not name:
        raise RuntimeError(
            "snd-aloop Loopback card not found; load with: "
            "sudo modprobe snd-aloop enable=1,1 index=10"
        )
    return name


def _find_loopback_card() -> str | None:
    """Prefer card id exactly ``Loopback`` (index=10), else any Loopback*."""
    try:
        text = Path("/proc/asound/cards").read_text(encoding="utf-8")
    except OSError:
        return None
    exact = None
    any_lb = None
    for m in re.finditer(r"^\s*\d+\s+\[([^\]]+)\]", text, re.M):
        cid = m.group(1).strip()
        if cid == "Loopback":
            exact = cid
        elif "Loopback" in cid and any_lb is None:
            any_lb = cid
    return exact or any_lb


def write_sip_line_asoundrc(home: Path, *, loopback_card: str | None = None) -> str:
    """Point pjsua default PCM at the virtual SIP line (not the handset).

    Playback → Loopback,0,0 ; capture ← Loopback,1,1 (second cable of the pair).
    The handset bridge uses the opposite ends of those cables.

    Used for inbound / voicemail so the cradle stays dark until we bridge.
    """
    card = loopback_card or ensure_loopback_card()
    (home / ".asoundrc").write_text(
        "\n".join(
            [
                "# operator-os: SIP softphone on snd-aloop (handset never default)",
                "pcm.!default {",
                "  type asym",
                "  playback.pcm \"line_play\"",
                "  capture.pcm \"line_cap\"",
                "}",
                "pcm.line_play {",
                "  type plug",
                f'  slave.pcm "hw:{card},0,0"',
                "}",
                "pcm.line_cap {",
                "  type plug",
                f'  slave.pcm "hw:{card},1,1"',
                "}",
                "ctl.!default {",
                "  type hw",
                f'  card "{card}"',
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return card


def write_handset_asoundrc(home: Path, handset_alsa: str) -> None:
    """Point pjsua default PCM at the USB cradle (outbound live calls).

    Avoids alsaloop: ATR2x full-speed USB underruns Loopback↔plughw hard enough
    that Meet audio arrives on the SIP line but the earpiece stays silent.

    Playback and capture are separate asym slaves (same device). The ATR2x still
    electrically mixes speaker→mic when both are open — see ``HandsetSipGuard``.
    """
    dev = (handset_alsa or "plughw:2,0").strip() or "plughw:2,0"
    (home / ".asoundrc").write_text(
        "\n".join(
            [
                "# operator-os: outbound SIP on USB handset (no alsaloop)",
                "pcm.!default {",
                "  type asym",
                '  playback.pcm "handset"',
                '  capture.pcm "handset"',
                "}",
                "pcm.handset {",
                "  type plug",
                f'  slave.pcm "{dev}"',
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def alsa_card_from_device(handset_alsa: str) -> str:
    """``plughw:2,0`` / ``hw:ATR2xUSB,0`` → card id for amixer."""
    m = re.match(r"(?:plug)?hw:([^,]+)", (handset_alsa or "").strip())
    return m.group(1) if m else "2"


@dataclass
class HandsetSipGuard:
    """ATR2x TX hygiene for outbound SIP.

    The adapter feeds speaker into mic at near full scale when capture gain is
    maxed (not acoustic bleed — digital/electrical). Lower capture for the call,
    force mic-playback off, and optionally mute TX while Meet plays join IVR.
    """

    handset_alsa: str = "plughw:2,0"
    capture_level: int = 12  # ALSA Mic capture 0–30; 30 loops Meet into the room
    _card: str = field(init=False, repr=False)
    _saved_capture: int | None = field(default=None, init=False, repr=False)
    _saved_cap_on: bool | None = field(default=None, init=False, repr=False)
    _saved_play_on: bool | None = field(default=None, init=False, repr=False)
    _unmute_timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._card = alsa_card_from_device(self.handset_alsa)

    def arm(self) -> None:
        """Save mixer; mic-playback off; drop capture gain."""
        with self._lock:
            self._cancel_unmute_locked()
            self._saved_capture = self._get_capture_level()
            self._saved_cap_on = self._get_switch("capture")
            self._saved_play_on = self._get_switch("playback")
            self._amixer("sset", "Mic", "playback", "off")
            level = max(0, min(30, int(self.capture_level)))
            self._amixer("sset", "Mic", "capture", str(level))
            self._amixer("sset", "Mic", "cap")

    def mute_tx(self) -> None:
        with self._lock:
            self._cancel_unmute_locked()
            self._amixer("sset", "Mic", "nocap")

    def unmute_tx_after(self, seconds: float) -> None:
        """Keep TX muted, then unmute (Meet join announcements)."""
        with self._lock:
            self._cancel_unmute_locked()
            self._amixer("sset", "Mic", "nocap")
            delay = max(0.0, float(seconds))
            if delay <= 0:
                self._amixer("sset", "Mic", "cap")
                return
            t = threading.Timer(delay, self._unmute_tx_safe)
            t.daemon = True
            t.start()
            self._unmute_timer = t

    def release(self) -> None:
        """Restore mixer (call from hangup)."""
        with self._lock:
            self._cancel_unmute_locked()
            if self._saved_capture is not None:
                self._amixer("sset", "Mic", "capture", str(self._saved_capture))
            if self._saved_cap_on is True:
                self._amixer("sset", "Mic", "cap")
            elif self._saved_cap_on is False:
                self._amixer("sset", "Mic", "nocap")
            if self._saved_play_on is True:
                self._amixer("sset", "Mic", "playback", "on")
            elif self._saved_play_on is False:
                self._amixer("sset", "Mic", "playback", "off")
            self._saved_capture = None
            self._saved_cap_on = None
            self._saved_play_on = None

    def _unmute_tx_safe(self) -> None:
        with self._lock:
            self._unmute_timer = None
            self._amixer("sset", "Mic", "cap")

    def _cancel_unmute_locked(self) -> None:
        t = self._unmute_timer
        self._unmute_timer = None
        if t is not None:
            t.cancel()

    def _amixer(self, *args: str) -> None:
        try:
            subprocess.run(
                ["amixer", "-c", self._card, *args],
                check=False,
                capture_output=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _get_capture_level(self) -> int | None:
        try:
            out = subprocess.run(
                ["amixer", "-c", self._card, "sget", "Mic"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        # Mono: Playback … Capture 30 [100%] [33.00dB] [on]
        m = re.search(r"Capture\s+(\d+)\s+\[", out)
        return int(m.group(1)) if m else None

    def _get_switch(self, which: str) -> bool | None:
        """which = capture|playback → True if unmuted/on."""
        try:
            out = subprocess.run(
                ["amixer", "-c", self._card, "sget", "Mic"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        if which == "playback":
            m = re.search(
                r"Playback\s+\d+\s+\[[^\]]+\]\s+\[[^\]]+\]\s+\[(on|off)\]", out
            )
            return m.group(1) == "on" if m else None
        m = re.search(
            r"Capture\s+\d+\s+\[[^\]]+\]\s+\[[^\]]+\]\s+\[(on|off)\]", out
        )
        return m.group(1) == "on" if m else None


@dataclass
class HandsetBridge:
    """alsaloop jobs joining virtual line ↔ USB handset. Inbound live answer only."""

    handset_alsa: str = "plughw:2,0"
    loopback_card: str | None = None
    rate: int = 8000
    # 100ms — ATR2x full-speed can't sustain 20ms without underrun silence.
    latency_us: int = 100_000
    _procs: list[subprocess.Popen[bytes]] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def active(self) -> bool:
        with self._lock:
            return any(p.poll() is None for p in self._procs)

    def start(self) -> None:
        with self._lock:
            if any(p.poll() is None for p in self._procs):
                return
            card = self.loopback_card or ensure_loopback_card()
            self.loopback_card = card
            handset = self.handset_alsa
            lat = str(self.latency_us)
            # Cable A: softphone play (0,0) → capture (1,0) → handset speaker
            # Cable B: handset mic → play (0,1) → softphone capture (1,1)
            jobs = [
                [
                    "alsaloop",
                    "-C",
                    f"hw:{card},1,0",
                    "-P",
                    handset,
                    "-r",
                    str(self.rate),
                    "-c",
                    "1",
                    "-f",
                    "S16_LE",
                    "-t",
                    lat,
                    "-n",
                    "-S",
                    "1",
                ],
                [
                    "alsaloop",
                    "-C",
                    handset,
                    "-P",
                    f"hw:{card},0,1",
                    "-r",
                    str(self.rate),
                    "-c",
                    "1",
                    "-f",
                    "S16_LE",
                    "-t",
                    lat,
                    "-n",
                    "-S",
                    "1",
                ],
            ]
            procs: list[subprocess.Popen[bytes]] = []
            try:
                for cmd in jobs:
                    procs.append(
                        subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    )
            except OSError as e:
                for p in procs:
                    p.kill()
                raise RuntimeError(f"handset bridge failed to start: {e}") from e
            time.sleep(0.15)
            dead = [p for p in procs if p.poll() is not None]
            if dead:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                raise RuntimeError("handset bridge alsaloop exited immediately")
            self._procs = procs

    def stop(self) -> None:
        with self._lock:
            procs = self._procs
            self._procs = []
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
