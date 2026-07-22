"""Off-hook handset bridge: virtual SIP line (snd-aloop) ↔ USB cradle.

Architecture:
  Softphone (pjsua) always opens the Loopback card — never the handset.
  On-hook features (voicemail) use that line alone; the cradle stays dark.
  Only a live call starts ``alsaloop`` to join Loopback ↔ ``plughw`` handset.
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


@dataclass
class HandsetBridge:
    """alsaloop jobs joining virtual line ↔ USB handset. Off-hook / live SIP only."""

    handset_alsa: str = "plughw:2,0"
    loopback_card: str | None = None
    rate: int = 8000
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
                    "20000",
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
                    "20000",
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
