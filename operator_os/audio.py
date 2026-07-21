"""Sole owner of handset audio subprocesses (aplay, arecord, Piper, espeak)."""

from __future__ import annotations

import array
import io
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from operator_os.config import AudioConfig

ROOT = Path(__file__).resolve().parent.parent
VOICES_DIR = ROOT / "voices"
FX_DIR = ROOT / "data" / "fx"

# Named voices: onnx + matching .onnx.json beside it.
VOICE_MODELS: dict[str, Path] = {
    "hfc": VOICES_DIR / "hfc_female" / "en_US-hfc_female-medium.onnx",
    "hfc_female": VOICES_DIR / "hfc_female" / "en_US-hfc_female-medium.onnx",
    "en_US-hfc_female-medium": VOICES_DIR / "hfc_female" / "en_US-hfc_female-medium.onnx",
}

_TONE_CHUNK_MS = 20
_TONE_AMP = 0.15
# Crossbar seize timing (see docs/crossbar-outside-line-effect.md).
_SEIZE_POST_DIGIT_MS = 50
_SEIZE_BLIND_MS = 150
_SEIZE_FADE_IN_MS = 200


def resolve_voice(name_or_path: str) -> Path:
    """Resolve a voice name or filesystem path to an onnx model."""
    if name_or_path in VOICE_MODELS:
        path = VOICE_MODELS[name_or_path]
    else:
        path = Path(name_or_path)
        if not path.is_absolute():
            path = ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Piper voice not found: {path}")
    return path


def resolve_stream_url(url: str, *, timeout: float = 8.0) -> str:
    """Return a playable media URL; expand ``.pls`` playlists to File1."""
    import urllib.request

    u = (url or "").strip()
    if not u:
        raise ValueError("empty stream url")
    lower = u.lower()
    if not (lower.endswith(".pls") or ".pls?" in lower or "/pls" in lower):
        return u
    req = urllib.request.Request(u, headers={"User-Agent": "operator-os/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    for line in body.splitlines():
        if line.lower().startswith("file1="):
            media = line.split("=", 1)[1].strip()
            if media:
                return media
    raise RuntimeError(f"no File1 in playlist: {u}")


class AudioRouter:
    """Process-wide audio lock. No other module may call aplay/arecord/Piper."""

    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._helper_proc: subprocess.Popen[bytes] | None = None
        self._capture_proc: subprocess.Popen[bytes] | None = None
        self._temps: list[Path] = []
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._capture_thread: threading.Thread | None = None
        self._duplex = False
        self._stop_gen = 0  # bumped on every stop(); long ops abort if gen changes
        self._on_hook = True
        self.engine = "unloaded"
        self.model_path: Path | None = None
        self._piper = None
        self._ensure_tts()

    def _ensure_tts(self) -> None:
        if self.engine != "unloaded":
            return
        try:
            self.model_path = resolve_voice(self.cfg.piper_voice)
            from piper import PiperVoice

            self._piper = PiperVoice.load(str(self.model_path))
            self.engine = "piper"
        except Exception:
            self._piper = None
            self.engine = "espeak"
            self.model_path = None

    def set_hook(self, off_hook: bool) -> None:
        self._on_hook = not off_hook
        if self._on_hook:
            self.stop()

    @property
    def is_on_hook(self) -> bool:
        return self._on_hook

    def notify_hangup(self) -> None:
        """Physical hangup = hardware cutoff. Mark on-hook and kill audio NOW.

        Safe from the GPIO callback thread. State machines may still run later;
        this alone must silence the receiver and block new playback.
        """
        self._on_hook = True
        self.stop()

    def stop(self) -> None:
        with self._lock:
            self._stop_gen += 1
            self._stop_locked()

    def close(self) -> None:
        self.stop()

    def is_busy(self) -> bool:
        """True while aplay/arecord (or duplex) is still running."""
        if self._duplex:
            return True
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _stopped_since(self, gen: int) -> bool:
        return self._stop_gen != gen

    def _interruptible_sleep(self, seconds: float, gen: int) -> bool:
        """Sleep in short slices; return False if stop()/hangup interrupted."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stopped_since(gen):
                return False
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        return not self._stopped_since(gen)

    def play_tone(
        self,
        name_or_hz: str | float | tuple[float, ...],
        seconds: float = 2.0,
        wait: bool = True,
        *,
        fade_in_ms: int = 0,
    ) -> None:
        """Play a tone. Dial tone streams into aplay until stop(); no WAV prebuild."""
        if self._on_hook:
            return
        freqs = _tone_freqs(name_or_hz)
        key = str(name_or_hz).lower() if isinstance(name_or_hz, str) else ""
        # Continuous dial tone: stream forever until audio.stop().
        if key in ("dial", "dial_tone") and not wait:
            self._start_tone_stream(freqs, duration_s=None, wait=False, fade_in_ms=fade_in_ms)
            return
        self._start_tone_stream(freqs, duration_s=seconds, wait=wait, fade_in_ms=fade_in_ms)

    def play_plant(self, name: str, *, wait: bool = True) -> None:
        """Play a named line-plant signature from a transition action.

        Catalog (extend here when edges need distinct throws):
          fx_seize   — patch onto a trunk / operator jack
          fx_release — drop trunk, back to dial tone / listen
          fx_outside — outside-line seize (digit 9)
        """
        key = str(name or "").strip().lower()
        if key in ("fx_seize", "crossbar"):
            self.play_crossbar_click(kind="seize", wait=wait)
            return
        if key == "fx_release":
            self.play_crossbar_click(kind="release", wait=wait)
            return
        if key == "fx_outside":
            self.seize_outside_line()
            return

    def play_crossbar_click(self, *, kind: str = "seize", wait: bool = True) -> None:
        """Electromechanical switch throw into the handset receiver."""
        if self._on_hook:
            return
        rate = self.cfg.sample_rate_hz
        click = load_fx_pcm(kind, rate) or build_crossbar_click(rate)
        duration = len(click) / (2.0 * rate)
        if self._duplex:
            self.write_duplex_playback(click, src_rate=rate)
            if wait:
                self._interruptible_sleep(duration + 0.05, self._stop_gen)
            return
        self._play_pcm_raw(click, rate, wait=wait)

    def seize_outside_line(self) -> None:
        """Electromechanical seize: click/thud → blind spot → external CO dial tone.

        See docs/crossbar-outside-line-effect.md. Abort immediately if hangup
        interrupts mid-sequence.
        """
        if self._on_hook:
            return
        self.stop()
        gen = self._stop_gen
        if self._on_hook or not self._interruptible_sleep(_SEIZE_POST_DIGIT_MS / 1000.0, gen):
            return
        self.play_crossbar_click(wait=True)
        if self._on_hook or self._stopped_since(gen):
            return
        if not self._interruptible_sleep(_SEIZE_BLIND_MS / 1000.0, gen):
            return
        if self._on_hook:
            return
        self._start_tone_stream(
            (350.0, 440.0),
            duration_s=None,
            wait=False,
            fade_in_ms=_SEIZE_FADE_IN_MS,
        )

    def _start_tone_stream(
        self,
        freqs: tuple[float, ...],
        duration_s: float | None,
        wait: bool,
        fade_in_ms: int = 0,
    ) -> None:
        if self._on_hook:
            return
        with self._lock:
            if self._on_hook:
                return
            self._stop_locked()
            self._stream_stop.clear()
            rate = self.cfg.sample_rate_hz
            self._proc = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.cfg.alsa_device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(rate),
                    "-t",
                    "raw",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            thread = threading.Thread(
                target=self._tone_writer,
                args=(freqs, rate, duration_s, fade_in_ms, self._proc),
                daemon=True,
            )
            self._stream_thread = thread
            thread.start()
        if wait:
            thread.join()
            with self._lock:
                if self._stream_thread is thread:
                    self._stream_thread = None
                if self._proc is not None and self._proc.poll() is not None:
                    self._proc = None

    def _tone_writer(
        self,
        freqs: tuple[float, ...],
        rate: int,
        duration_s: float | None,
        fade_in_ms: int,
        proc: subprocess.Popen[bytes],
    ) -> None:
        chunk = max(2, int(rate * _TONE_CHUNK_MS / 1000))
        sample_i = 0
        end_i = None if duration_s is None else int(rate * duration_s)
        fade_n = max(0, int(rate * fade_in_ms / 1000.0))
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            while not self._stream_stop.is_set() and not self._on_hook:
                if end_i is not None and sample_i >= end_i:
                    break
                n = chunk
                if end_i is not None:
                    n = min(chunk, end_i - sample_i)
                buf = array.array("h")
                for i in range(sample_i, sample_i + n):
                    t = i / rate
                    val = sum(math.sin(2 * math.pi * f * t) for f in freqs) / max(1, len(freqs))
                    if fade_n > 0 and i < fade_n:
                        val *= i / fade_n
                    buf.append(int(max(-1.0, min(1.0, val * _TONE_AMP)) * 32767))
                sample_i += n
                try:
                    stdin.write(buf.tobytes())
                except BrokenPipeError:
                    break
            try:
                stdin.close()
            except Exception:
                pass
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def _play_pcm_raw(self, pcm: bytes, rate: int, wait: bool = True) -> None:
        """Play mono S16_LE raw PCM once through aplay."""
        if self._on_hook:
            return
        with self._lock:
            if self._on_hook:
                return
            self._stop_locked()
            self._proc = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.cfg.alsa_device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(rate),
                    "-t",
                    "raw",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert self._proc.stdin is not None
            try:
                self._proc.stdin.write(pcm)
                self._proc.stdin.close()
            except BrokenPipeError:
                pass
            if wait:
                self._proc.wait()
                self._proc = None

    def play_file(self, path: Path | str, wait: bool = True, *, ephemeral: bool = False) -> None:
        if self._on_hook:
            if ephemeral:
                Path(path).unlink(missing_ok=True)
            return
        with self._lock:
            if self._on_hook:
                if ephemeral:
                    Path(path).unlink(missing_ok=True)
                return
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

    def play_stream(self, url: str, *, wait: bool = False) -> None:
        """Seize a live HTTP(S) audio stream into the handset (ffmpeg → aplay).

        Accepts a direct media URL or a Shoutcast/Icecast ``.pls`` playlist.
        Runs until hangup/stop, or until the remote end closes.
        """
        if self._on_hook:
            return
        media = resolve_stream_url(url)
        rate = self.cfg.sample_rate_hz
        with self._lock:
            if self._on_hook:
                return
            self._stop_locked()
            ff = subprocess.Popen(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    media,
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-ac",
                    "1",
                    "-ar",
                    str(rate),
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert ff.stdout is not None
            ap = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.cfg.alsa_device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(rate),
                    "-t",
                    "raw",
                ],
                stdin=ff.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ff.stdout.close()
            self._helper_proc = ff
            self._proc = ap
        if wait:
            proc = self._proc
            if proc is not None:
                proc.wait()
                with self._lock:
                    if self._proc is proc:
                        self._proc = None
                        self._helper_proc = None

    def speak(self, text: str, *, wait: bool = True) -> None:
        if self._on_hook:
            return
        text = text.strip()
        if not text:
            return
        wav = self.synthesize(text)
        if wav is None:
            return
        if self._on_hook:
            wav.unlink(missing_ok=True)
            return
        self.play_file(wav, wait=wait, ephemeral=True)

    def synthesize(self, text: str, output_path: Path | str | None = None) -> Path | None:
        """Render speech to WAV at cfg.sample_rate_hz (resample Piper/espeak if needed)."""
        self._ensure_tts()
        text = text.strip()
        if not text:
            return None
        if output_path is None:
            fd, name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            dest = Path(name)
            dest.unlink(missing_ok=True)
        else:
            dest = Path(output_path)
            dest.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        raw = Path(tmp_name)
        raw.unlink(missing_ok=True)

        try:
            wrote = False
            if self._piper is not None:
                try:
                    _synthesize_piper(self._piper, text, raw, self.cfg.piper_volume)
                    wrote = raw.is_file() and raw.stat().st_size > 0
                except Exception:
                    raw.unlink(missing_ok=True)
                    wrote = False
            if not wrote and not _synthesize_espeak(text, raw):
                return None
            ensure_wav_rate(raw, dest, self.cfg.sample_rate_hz)
            return dest
        except Exception:
            dest.unlink(missing_ok=True)
            return None
        finally:
            if raw.resolve() != dest.resolve():
                raw.unlink(missing_ok=True)

    def start_duplex(self, on_capture: object) -> None:
        """Start handset capture + playback for Realtime. on_capture(pcm_s16_handset_rate)."""
        if self._on_hook:
            raise RuntimeError("duplex disabled while on-hook")
        rate = self.cfg.sample_rate_hz
        # ~100ms chunks at handset rate
        chunk = max(2, int(rate * 0.1) * 2)

        def _reader(proc: subprocess.Popen[bytes], gen: int) -> None:
            assert proc.stdout is not None
            try:
                while not self._stream_stop.is_set() and not self._stopped_since(gen) and not self._on_hook:
                    data = proc.stdout.read(chunk)
                    if not data:
                        break
                    try:
                        on_capture(data)  # type: ignore[operator]
                    except Exception:
                        break
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass

        with self._lock:
            self._stop_locked()
            self._stream_stop.clear()
            gen = self._stop_gen
            self._duplex = True
            self._capture_proc = subprocess.Popen(
                [
                    "arecord",
                    "-q",
                    "-D",
                    self.cfg.alsa_device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(rate),
                    "-t",
                    "raw",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._proc = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.cfg.alsa_device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(rate),
                    "-t",
                    "raw",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._capture_thread = threading.Thread(
                target=_reader,
                args=(self._capture_proc, gen),
                daemon=True,
                name="duplex-capture",
            )
            self._capture_thread.start()

    def write_duplex_playback(self, pcm_s16: bytes, *, src_rate: int) -> None:
        """Write PCM into the duplex aplay stdin (resampled to handset rate)."""
        if self._on_hook or not pcm_s16 or not self._duplex:
            return
        rate = self.cfg.sample_rate_hz
        pcm = pcm_s16 if src_rate == rate else resample_s16_mono(pcm_s16, src_rate, rate)
        with self._lock:
            if self._on_hook:
                return
            proc = self._proc
            if proc is None or proc.stdin is None or proc.poll() is not None:
                return
            try:
                proc.stdin.write(pcm)
                proc.stdin.flush()
            except BrokenPipeError:
                pass

    def reset_duplex_playback(self) -> None:
        """Drop in-flight model audio (barge-in): restart aplay, keep arecord."""
        if not self._duplex:
            return
        rate = self.cfg.sample_rate_hz
        with self._lock:
            old = self._proc
            self._proc = None
            if old is not None:
                if old.poll() is None:
                    old.kill()
                if old.stdin is not None:
                    try:
                        old.stdin.close()
                    except Exception:
                        pass
            self._proc = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.cfg.alsa_device,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(rate),
                    "-t",
                    "raw",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def record(self, seconds: float, output_path: Path | str) -> Path:
        """Record from the handset mic. Interruptible via stop()/hangup."""
        if self._on_hook:
            raise RuntimeError("mic capture is disabled while on-hook")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._stop_locked()
            gen = self._stop_gen
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
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc = self._proc
        try:
            while proc.poll() is None:
                if self._stopped_since(gen):
                    proc.kill()
                    break
                time.sleep(0.05)
            rc = proc.wait(timeout=1.0)
            if self._stopped_since(gen):
                raise RuntimeError("recording interrupted")
            if rc != 0:
                raise RuntimeError(f"arecord failed rc={rc}")
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        return out

    def _stop_locked(self) -> None:
        self._stream_stop.set()
        self._duplex = False
        for proc in (self._proc, self._helper_proc, self._capture_proc):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.kill()
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None
        self._helper_proc = None
        self._capture_proc = None
        self._stream_thread = None
        self._capture_thread = None
        self._clear_temps()

    def _clear_temps(self) -> None:
        for p in self._temps:
            p.unlink(missing_ok=True)
        self._temps.clear()


def ensure_wav_rate(src: Path, dest: Path, target_hz: int) -> None:
    """Resample mono S16 WAV to target_hz, or copy if already correct.

    Rejects non-mono / non-16-bit rather than guessing channel layouts.
    """
    with wave.open(str(src), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if channels != 1 or width != 2:
        raise ValueError(f"unsupported WAV format: channels={channels} width={width}")
    if rate == target_hz:
        if src.resolve() != Path(dest).resolve():
            dest.write_bytes(src.read_bytes())
        return
    pcm = resample_s16_mono(frames, rate, target_hz)
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(target_hz)
        out.writeframes(pcm)


def resample_s16_mono(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear resample S16 little-endian mono. Good enough for telephony TTS."""
    if src_rate == dst_rate:
        return pcm
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError(f"invalid rates src={src_rate} dst={dst_rate}")
    src = array.array("h")
    src.frombytes(pcm)
    if not src:
        return b""
    n_dst = max(1, int(round(len(src) * dst_rate / src_rate)))
    dst = array.array("h", [0] * n_dst)
    last = len(src) - 1
    scale = src_rate / dst_rate
    for i in range(n_dst):
        x = i * scale
        j = int(x)
        if j >= last:
            dst[i] = src[last]
            continue
        frac = x - j
        dst[i] = int(src[j] * (1.0 - frac) + src[j + 1] * frac)
    return dst.tobytes()


def _synthesize_piper(voice, text: str, path: Path, volume: float) -> None:
    from piper.config import SynthesisConfig

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file, syn_config=SynthesisConfig(volume=volume))
    path.write_bytes(buf.getvalue())


def _synthesize_espeak(text: str, path: Path) -> bool:
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        return False
    try:
        subprocess.run(
            [espeak, "-w", str(path), "-a", "40", text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return path.is_file() and path.stat().st_size > 0
    except (OSError, subprocess.CalledProcessError):
        path.unlink(missing_ok=True)
        return False


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


def load_fx_pcm(kind: str, rate: int) -> bytes | None:
    """Load a short plant-FX WAV from ``data/fx/``, resampled to ``rate``.

    ``kind`` is ``seize`` / ``release``. Seize picks randomly among
    ``fx_seize*.wav`` so successive plant throws don't sound identical.
    Returns None if missing so callers can fall back to the synth click.
    """
    pattern = "fx_release*.wav" if kind == "release" else "fx_seize*.wav"
    paths = sorted(FX_DIR.glob(pattern))
    if not paths:
        return None
    path = random.choice(paths)
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            src_rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except (OSError, wave.Error):
        return None
    if not pcm:
        return None
    if src_rate == rate:
        return pcm
    # Linear resample mono S16 (clips are tiny; quality is fine).
    src = array.array("h")
    src.frombytes(pcm)
    if not src:
        return None
    n_out = max(1, int(round(len(src) * rate / src_rate)))
    out = array.array("h", [0] * n_out)
    last = len(src) - 1
    for i in range(n_out):
        x = i * src_rate / rate
        j = int(x)
        frac = x - j
        a = src[j] if j <= last else 0
        b = src[j + 1] if j < last else a
        out[i] = int(a + (b - a) * frac)
    return out.tobytes()


def build_crossbar_click(rate: int, *, seed: int = 1) -> bytes:
    """Synthesize a mechanical switch throw (mono S16_LE).

    Two distinct clicks: armature pull-in, gap, then contact slap + ring.
    Leading silence covers USB/ALSA open latency so the transient is not eaten.
    """
    rng = random.Random(seed)
    lead = int(rate * 0.080)
    gap = int(rate * 0.045)
    stage1 = int(rate * 0.055)
    stage2 = int(rate * 0.090)
    tail = int(rate * 0.040)
    n = lead + stage1 + gap + stage2 + tail
    mix = [0.0] * n

    def _add_noise(start: int, dur_s: float, amp: float, tau_s: float) -> None:
        dur = int(rate * dur_s)
        tau = max(1.0, rate * tau_s)
        for i in range(dur):
            idx = start + i
            if idx >= n:
                break
            env = math.exp(-i / tau)
            mix[idx] += (rng.random() * 2.0 - 1.0) * amp * env

    def _add_sine(start: int, dur_s: float, hz: float, amp: float, tau_s: float) -> None:
        dur = int(rate * dur_s)
        tau = max(1.0, rate * tau_s)
        for i in range(dur):
            idx = start + i
            if idx >= n:
                break
            env = math.exp(-i / tau)
            mix[idx] += math.sin(2 * math.pi * hz * i / rate) * amp * env

    def _add_pop(start: int, dur_s: float, amp: float) -> None:
        dur = max(1, int(rate * dur_s))
        for i in range(dur):
            idx = start + i
            if idx >= n:
                break
            mix[idx] += amp if (i % 2 == 0) else -amp

    # --- Click 1: armature pull-in (duller, lower) ---
    a0 = lead
    _add_sine(a0, 0.045, 90.0, 0.50, 0.018)
    _add_sine(a0, 0.035, 50.0, 0.32, 0.015)
    _add_noise(a0, 0.012, 0.28, 0.005)
    _add_pop(a0, 0.0012, 0.40)

    # --- Click 2: contacts slam (brighter, after a clear gap) ---
    c0 = lead + stage1 + gap
    _add_pop(c0, 0.0015, 0.90)
    _add_noise(c0, 0.016, 0.72, 0.0035)
    _add_sine(c0, 0.065, 145.0, 0.58, 0.018)
    _add_sine(c0, 0.040, 1200.0, 0.30, 0.009)
    _add_sine(c0, 0.030, 2600.0, 0.18, 0.005)
    # Contact bounce ~14 ms later
    b0 = c0 + int(rate * 0.014)
    _add_pop(b0, 0.0010, 0.40)
    _add_noise(b0, 0.008, 0.25, 0.0025)

    out = array.array("h")
    for v in mix:
        out.append(int(max(-1.0, min(1.0, v)) * 32767))
    return out.tobytes()


def _write_tone_wav(freqs: tuple[float, ...], rate: int, seconds: float) -> Path:
    n = int(rate * seconds)
    amp = 0.15
    samples = array.array("h")
    samples.extend(
        int(
            max(
                -1.0,
                min(
                    1.0,
                    (sum(math.sin(2 * math.pi * f * (i / rate)) for f in freqs) / max(1, len(freqs)))
                    * amp,
                ),
            )
            * 32767
        )
        for i in range(n)
    )
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    path = Path(name)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return path


@dataclass(frozen=True)
class WavLevels:
    path: Path
    sample_rate_hz: int
    duration_s: float
    peak: int
    rms: float
    peak_dbfs: float
    rms_dbfs: float
    clip_samples: int
    verdict: str  # OK | WARN | BAD
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict != "BAD"


def analyze_wav_levels(path: Path | str) -> WavLevels:
    """Peak/RMS/clip check for mono S16 recordings (mic diagnostics)."""
    p = Path(path)
    with wave.open(str(p), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        pcm = wf.readframes(nframes)
    if channels != 1 or width != 2:
        raise ValueError(f"need mono S16 WAV, got channels={channels} width={width}")

    samples = array.array("h")
    samples.frombytes(pcm)
    n = len(samples)
    full = 32768.0
    peak = max((abs(s) for s in samples), default=0)
    # abs(-32768) overflows signed 16; treat as full-scale.
    if peak > 32767:
        peak = 32768
    rms = math.sqrt(sum(int(s) * int(s) for s in samples) / n) if n else 0.0
    peak_db = _dbfs(peak, full)
    rms_db = _dbfs(rms, full)
    clips = sum(1 for s in samples if abs(int(s)) >= 32000)
    duration = nframes / rate if rate else 0.0

    if peak == 0:
        verdict, detail = "BAD", "silence (no signal)"
    elif clips > max(1, int(rate * 0.01)):
        verdict, detail = "BAD", f"heavy clipping ({clips} hot samples)"
    elif peak_db > -1.0:
        verdict, detail = "WARN", "very hot / near clipping — ease mic drive pot"
    elif peak_db < -35:
        verdict, detail = "WARN", "very quiet — raise mic drive or speak louder"
    elif rms_db < -45:
        verdict, detail = "WARN", "low average level — usable but thin"
    else:
        verdict, detail = "OK", "usable carbon-mic level"

    return WavLevels(
        path=p,
        sample_rate_hz=rate,
        duration_s=duration,
        peak=peak,
        rms=rms,
        peak_dbfs=peak_db,
        rms_dbfs=rms_db,
        clip_samples=clips,
        verdict=verdict,
        detail=detail,
    )


def _dbfs(level: float, full: float = 32768.0) -> float:
    if level <= 0:
        return float("-inf")
    return 20.0 * math.log10(level / full)
