"""Sole owner of handset audio subprocesses (aplay, arecord, Piper, espeak)."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

from operator_os.config import AudioConfig


class AudioRouter:
    """Process-wide audio lock. No other module may call aplay/arecord/Piper."""

    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._temps: list[Path] = []
        self._on_hook = True

    def set_hook(self, off_hook: bool) -> None:
        self._on_hook = not off_hook
        if self._on_hook:
            self.stop()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def play_tone(
        self,
        name_or_hz: str | float | tuple[float, ...],
        seconds: float = 2.0,
        wait: bool = True,
    ) -> None:
        rates = _tone_freqs(name_or_hz)
        path = _write_tone_wav(rates, self.cfg.sample_rate_hz, seconds)
        self.play_file(path, wait=wait, ephemeral=True)

    def play_file(self, path: Path | str, wait: bool = True, *, ephemeral: bool = False) -> None:
        with self._lock:
            self._stop_locked()
            p = Path(path)
            if ephemeral:
                self._temps.append(p)
            self._proc = subprocess.Popen(
                ["aplay", "-q", "-D", self.cfg.alsa_device, str(p)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if wait and self._proc is not None:
                self._proc.wait()
                self._proc = None
                self._clear_temps()

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        wav = _synthesize(text, self.cfg)
        if wav is None:
            return
        self.play_file(wav, wait=True, ephemeral=True)

    def record(self, seconds: float, output_path: Path | str) -> Path:
        if self._on_hook:
            raise RuntimeError("mic capture is disabled while on-hook")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._stop_locked()
            cmd = [
                "arecord",
                "-q",
                "-D",
                self.cfg.alsa_device,
                "-f",
                self.cfg.format,
                "-r",
                str(self.cfg.sample_rate_hz),
                "-c",
                str(self.cfg.channels),
                "-d",
                str(max(1, int(seconds))),
                str(out),
            ]
            subprocess.run(cmd, check=True)
        return out

    def _stop_locked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._clear_temps()

    def _clear_temps(self) -> None:
        for p in self._temps:
            p.unlink(missing_ok=True)
        self._temps.clear()


def _tone_freqs(name_or_hz: str | float | tuple[float, ...]) -> tuple[float, ...]:
    if isinstance(name_or_hz, (int, float)):
        return (float(name_or_hz),)
    if isinstance(name_or_hz, tuple):
        return name_or_hz
    name = str(name_or_hz).lower()
    if name in ("dial", "dial_tone"):
        return (350.0, 440.0)
    if name in ("busy", "reorder"):
        return (480.0, 620.0)
    if name in ("crossbar", "relay"):
        return (1200.0,)
    return (440.0,)


def _write_tone_wav(freqs: tuple[float, ...], rate: int, seconds: float) -> Path:
    n = int(rate * seconds)
    amp = 0.15
    frames = bytearray()
    for i in range(n):
        t = i / rate
        val = sum(math.sin(2 * math.pi * f * t) for f in freqs) / max(1, len(freqs))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, val * amp)) * 32767))
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    path = Path(name)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path


def _synthesize(text: str, cfg: AudioConfig) -> Path | None:
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    path = Path(name)
    path.unlink(missing_ok=True)

    piper = shutil.which("piper")
    if piper:
        try:
            subprocess.run(
                [piper, "--model", cfg.piper_voice, "--output_file", str(path)],
                input=text.encode(),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if path.is_file() and path.stat().st_size > 0:
                return path
        except (OSError, subprocess.CalledProcessError):
            path.unlink(missing_ok=True)

    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if espeak:
        try:
            subprocess.run(
                [espeak, "-w", str(path), "-a", "40", text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return path
        except (OSError, subprocess.CalledProcessError):
            path.unlink(missing_ok=True)
    return None
