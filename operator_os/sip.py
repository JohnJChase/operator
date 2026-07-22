"""Outside-line number helpers and Telnyx/pjsua SIP call session."""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PJSUA = ROOT / "tools" / "pjsua"
TELNYX_SIP_HOST = "sip.telnyx.com"
# Avoid default SIP 5060 locally — Telnyx only cares about *its* 5060.
LOCAL_SIP_PORT = 5080
LAST_LOG = Path(os.environ.get("OPERATOR_SIP_LAST_LOG", "/tmp/operator-last-pjsua.log"))
VM_DIR = ROOT / "data" / "voicemail"
ACTIVE_REC = VM_DIR / "_active.wav"
VM_GREETING_WAV = VM_DIR / "greeting.wav"
VM_GREETING_TEXT = (
    "The party you called is not available. Please leave a message after the tone."
)


def normalize_nanp(raw: str, *, default_region: str = "1") -> str | None:
    """Normalize US/NANP dial strings to +E.164. Returns None if invalid."""
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        if len(digits) < 8 or len(digits) > 15:
            return None
        return f"+{digits}"
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        return f"+{default_region}{digits}"
    if len(digits) == 11 and digits.startswith(default_region):
        return f"+{digits}"
    if 8 <= len(digits) <= 15:
        # International without + — require leading country code already present.
        return f"+{digits}"
    return None


_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}


def _speak_digit_group(digits: str) -> str:
    return " ".join(_DIGIT_WORDS.get(d, d) for d in digits)


def speak_phone_number(raw: str) -> str:
    """TTS-friendly phone: digit words, not a giant integer.

    NANP (+1XXXXXXXXXX) → "two zero two, five five five, one two one two".
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return "unknown"
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return (
            f"{_speak_digit_group(digits[:3])}, "
            f"{_speak_digit_group(digits[3:6])}, "
            f"{_speak_digit_group(digits[6:])}"
        )
    # International / odd lengths: spaced digit words (no comma-as-thousands).
    return _speak_digit_group(digits)


@dataclass
class SipCredentials:
    username: str
    password: str
    connection_id: str = ""
    registrar: str = TELNYX_SIP_HOST
    caller_id: str = ""  # E.164 Telnyx number for From / origination

    @classmethod
    def from_env(cls) -> SipCredentials | None:
        user = os.environ.get("TELNYX_SIP_USER", "").strip()
        password = os.environ.get("TELNYX_SIP_PASSWORD", "").strip()
        if not user or not password:
            return None
        return cls(
            username=user,
            password=password,
            connection_id=os.environ.get("TELNYX_CONNECTION_ID", "").strip(),
            caller_id=os.environ.get("TELNYX_CALLER_ID", "").strip(),
        )

    def local_id(self) -> str:
        """SIP From URI for outbound. Telnyx requires a number on the connection."""
        cid = self.caller_id.strip()
        if cid:
            if not cid.startswith("+"):
                norm = normalize_nanp(cid)
                cid = norm or cid
            return f"sip:{cid}@{self.registrar}"
        return f"sip:{self.username}@{self.registrar}"

    def register_id(self) -> str:
        """AOR for inbound REGISTER (credential username, not the DID)."""
        return f"sip:{self.username}@{self.registrar}"


def _resolve_pjsua(pjsua_path: Path = DEFAULT_PJSUA) -> Path:
    if pjsua_path.is_file() and os.access(pjsua_path, os.X_OK):
        return pjsua_path
    which = shutil.which("pjsua")
    if which:
        return Path(which)
    raise FileNotFoundError(
        f"pjsua not found at {pjsua_path}; build tools/pjsua (see docs/sip-outside-line.md)"
    )


def _write_asoundrc(home: Path, device: str, *, sound: str = "loopback") -> None:
    """Per-process ``~/.asoundrc`` for pjsua.

    Softphone stays on Loopback; plant jumpers join the handset for live calls.
    ``sound="handset"`` remains for diagnostics only.
    """
    from operator_os.handset_bridge import write_handset_asoundrc, write_sip_line_asoundrc

    if sound == "handset":
        write_handset_asoundrc(home, device)
    else:
        write_sip_line_asoundrc(home)


def _telnyx_reject_message(log_text: str) -> str | None:
    if "non-verified numbers" in log_text:
        return (
            "Telnyx blocked this destination (trial accounts can only call "
            "verified numbers). Verify the number in Mission Control, or upgrade."
        )
    if "Caller Origination Number is Invalid" in log_text:
        return (
            "Telnyx rejected caller ID. Set TELNYX_CALLER_ID to a number "
            "assigned to this SIP connection."
        )
    m = re.search(r"SIP/2\.0 403 ([^\r\n]+)", log_text)
    if m:
        return f"Telnyx rejected call: {m.group(1).strip()}"
    return None


def _pjsua_media_args(*, null_audio: bool) -> list[str]:
    """Shared media flags; ALSA device comes from per-process ``~/.asoundrc``.

    Inbound uses Loopback (+ optional HandsetBridge). Outbound uses handset
    asoundrc directly — see ``_write_asoundrc``.
    """
    if null_audio:
        return [
            "--null-audio",
            "--clock-rate=8000",
            "--no-vad",
            "--dis-codec=speex",
            "--dis-codec=ilbc",
            "--add-codec=pcmu",
            "--add-codec=pcma",
        ]
    return [
        "--clock-rate=8000",
        "--snd-clock-rate=8000",
        # WebRTC AEC3; long tail for handset acoustic coupling.
        "--ec-opt=4",
        "--ec-tail=800",
        "--capture-lat=40",
        "--playback-lat=40",
        "--snd-auto-close=0",
        "--no-vad",
        "--dis-codec=speex",
        "--dis-codec=ilbc",
        "--add-codec=pcmu",
        "--add-codec=pcma",
    ]


def sip_configured() -> bool:
    creds = SipCredentials.from_env()
    return creds is not None and bool(creds.caller_id.strip())


def ensure_voicemail_greeting(audio: object) -> Path:
    """Render greeting WAV at 8 kHz for pjsua conference play-file (no handset I/O)."""
    from operator_os.audio import ensure_wav_rate

    VM_DIR.mkdir(parents=True, exist_ok=True)
    if VM_GREETING_WAV.is_file() and VM_GREETING_WAV.stat().st_size > 500:
        return VM_GREETING_WAV
    synthesize = getattr(audio, "synthesize", None)
    if synthesize is None:
        raise RuntimeError("audio router cannot synthesize greeting")
    raw = synthesize(VM_GREETING_TEXT)
    if raw is None:
        raise RuntimeError("voicemail greeting synthesize failed")
    try:
        ensure_wav_rate(Path(raw), VM_GREETING_WAV, 8000)
        _append_beep_wav(VM_GREETING_WAV, hz=880.0, ms=350)
    finally:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            pass
    return VM_GREETING_WAV


def _append_beep_wav(path: Path, *, hz: float, ms: int) -> None:
    import array
    import math
    import wave

    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if channels != 1 or width != 2:
        return
    n = max(1, int(rate * (ms / 1000.0)))
    amp = 0.25
    beep = array.array("h")
    for i in range(n):
        # Short fade to avoid a click.
        env = 1.0
        if i < 40:
            env = i / 40.0
        elif i > n - 40:
            env = max(0.0, (n - i) / 40.0)
        sample = int(amp * env * 32767.0 * math.sin(2.0 * math.pi * hz * (i / rate)))
        beep.append(max(-32767, min(32767, sample)))
    silence = array.array("h", [0] * int(rate * 0.15))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames + silence.tobytes() + beep.tobytes())


def _cli_from_sip_log(text: str) -> str:
    """Best-effort E.164 from pjsua log (From / Contact)."""
    for pat in (
        r"sip:(\+\d{8,15})@",
        r"sip:(\d{10,15})@",
        r"From:\s*<sip:(\+?\d+)@",
        r"from.*?(\+1\d{10})\b",
    ):
        m = re.search(pat, text, re.I)
        if not m:
            continue
        got = normalize_nanp(m.group(1))
        if got:
            return got
    return ""


def _parse_conf_ports(log_text: str) -> dict[str, int]:
    """Map rough roles → conference port ids from ``cl`` output."""
    ports: dict[str, int] = {}
    for m in re.finditer(r"Port\s+#?\s*(\d+)\s*([^\n]*)", log_text, re.I):
        idx = int(m.group(1))
        line = m.group(2).lower()
        if "master" in line or "sound" in line:
            ports["sound"] = idx
        elif "_active" in line:
            ports["recorder"] = idx
        elif "greeting" in line:
            ports["file"] = idx
        elif ".wav" in line or "file" in line:
            ports.setdefault("file", idx)
        elif "sip:" in line or "call" in line:
            ports["call"] = idx
    if "call" not in ports:
        nums = [int(x) for x in re.findall(r"Port\s+#?\s*(\d+)", log_text)]
        skip = {ports.get("sound"), ports.get("file"), ports.get("recorder")}
        for n in nums:
            if n not in skip:
                ports["call"] = n
                break
    return ports


@dataclass
class _PjsuaProc:
    """Shared pjsua console process (PTY + log drain)."""

    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    null_audio: bool = False
    sound: str = "loopback"  # "loopback" | "handset"
    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _master: int | None = field(default=None, init=False, repr=False)
    _home: Path | None = field(default=None, init=False, repr=False)
    _log_path: Path | None = field(default=None, init=False, repr=False)
    _log_fp: object | None = field(default=None, init=False, repr=False)
    _log_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _log_text: str = field(default="", init=False, repr=False)
    _reader: threading.Thread | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def spawn(self, cmd: list[str]) -> None:
        with self._lock:
            if self._proc is not None:
                return
            self._home = Path(tempfile.mkdtemp(prefix="operator-sip-"))
            _write_asoundrc(self._home, self.alsa_device, sound=self.sound)
            self._log_path = self._home / "pjsua.log"
            self._log_fp = open(self._log_path, "w", encoding="utf-8")
            self._log_text = ""
            env = os.environ.copy()
            env["HOME"] = str(self._home)
            master, slave = pty.openpty()
            self._master = master
            self._proc = subprocess.Popen(
                cmd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                close_fds=True,
            )
            os.close(slave)
            self._reader = threading.Thread(target=self._drain_pty, daemon=True)
            self._reader.start()

    def is_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def write(self, data: bytes) -> None:
        master = self._master
        if master is None:
            raise RuntimeError("pjsua not running")
        os.write(master, data)

    def hangup(self) -> None:
        with self._lock:
            proc = self._proc
            master = self._master
            self._proc = None
            self._master = None
            home = self._home
            log_path = self._log_path
            log_fp = self._log_fp
            self._home = None
            self._log_path = None
            self._log_fp = None
        if master is not None:
            try:
                os.write(master, b"h\rq\r")
            except OSError:
                pass
        if proc is not None:
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        if master is not None:
            try:
                os.close(master)
            except OSError:
                pass
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
        if log_fp is not None:
            try:
                log_fp.close()
            except Exception:
                pass
        if log_path is not None and log_path.is_file():
            try:
                shutil.copyfile(log_path, LAST_LOG)
            except OSError:
                pass
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)

    def wait_log(self, needle: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if needle in self.read_log():
                return True
            if not self.is_alive():
                return needle in self.read_log()
            time.sleep(0.05)
        return needle in self.read_log()

    def read_log(self) -> str:
        with self._log_lock:
            return self._log_text

    def log_snippet(self) -> str:
        text = self.read_log()
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        interesting = [
            ln
            for ln in lines
            if any(
                k in ln.lower()
                for k in (
                    "registration",
                    "error",
                    "unauthorized",
                    "failed",
                    "403",
                    "origination",
                    "non-verified",
                    "incoming",
                )
            )
        ]
        pick = interesting[-3:] if interesting else lines[-3:]
        return " | ".join(pick)[:200]

    def _drain_pty(self) -> None:
        master = self._master
        if master is None:
            return
        fp = self._log_fp
        try:
            while True:
                r, _, _ = select.select([master], [], [], 0.5)
                if master not in r:
                    if not self.is_alive():
                        try:
                            while True:
                                r2, _, _ = select.select([master], [], [], 0)
                                if master not in r2:
                                    break
                                chunk = os.read(master, 8192)
                                if not chunk:
                                    break
                                self._append_log(chunk, fp)
                        except OSError:
                            pass
                        break
                    continue
                try:
                    chunk = os.read(master, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                self._append_log(chunk, fp)
        except Exception:
            pass

    def _append_log(self, chunk: bytes, fp: object | None) -> None:
        text = chunk.decode("utf-8", errors="replace")
        with self._log_lock:
            self._log_text += text
            if fp is not None:
                fp.write(text)  # type: ignore[union-attr]
                fp.flush()  # type: ignore[union-attr]


@dataclass
class SipCallSession:
    """One outbound Telnyx call via the bundled ``pjsua`` softphone.

    Outbound-only: no REGISTER (Telnyx rejects REGISTER when ``--id`` is the
    caller-ID number, and rejects INVITE when ``--id`` is the SIP username).
    Auth is digest on the INVITE via ``--outbound``.

    Plant chooses attach mode: ``sound="handset"`` for outbound (ATR2x cannot
    run alsaloop reliably); inbound live answer keeps Loopback + bridge.
    """

    e164: str
    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    progress_timeout_s: float = 5.0
    null_audio: bool = False
    dtmf_after_confirm: str = ""
    sound: str = "handset"
    _pj: _PjsuaProc | None = field(default=None, init=False, repr=False)
    _log_mark: int = field(default=0, init=False, repr=False)

    def start(self) -> None:
        if not self.credentials.caller_id.strip():
            raise RuntimeError(
                "TELNYX_CALLER_ID is required (Telnyx number for outbound caller ID)"
            )
        try:
            self._start_pjsua()
        except Exception:
            self.hangup()
            raise

    def _start_pjsua(self) -> None:
        pj = _PjsuaProc(
            credentials=self.credentials,
            alsa_device=self.alsa_device,
            pjsua_path=self.pjsua_path,
            null_audio=self.null_audio,
            sound=self.sound,
        )
        cmd = [
            str(_resolve_pjsua(self.pjsua_path)),
            "--log-level=4",
            "--app-log-level=3",
            "--no-color",
            f"--id={self.credentials.local_id()}",
            f"--outbound=sip:{self.credentials.registrar}",
            "--realm=*",
            f"--username={self.credentials.username}",
            f"--password={self.credentials.password}",
            f"--local-port={LOCAL_SIP_PORT}",
            "--use-srtp=0",
            "--srtp-secure=0",
            *_pjsua_media_args(null_audio=self.null_audio),
        ]
        pj.spawn(cmd)
        self._pj = pj
        if not pj.wait_log("active call", 5.0):
            if not pj.is_alive():
                detail = pj.log_snippet()
                raise RuntimeError(
                    "pjsua exited at startup" + (f": {detail}" if detail else "")
                )
        time.sleep(0.3)
        self._dial()
        self._check_early_progress()
        if self.dtmf_after_confirm:
            # Meet answers quickly, then plays "enter PIN" — digits sent too
            # early are discarded. Wait for CONFIRMED, then for the IVR.
            # TX echo from ATR2x is handled by plant hygiene+AEC (both legs),
            # not Meet-specific mute timers.
            deadline = time.monotonic() + 20.0
            confirmed = False
            while time.monotonic() < deadline:
                text = pj.read_log()
                if "CONFIRMED" in text or "state changed to CONFIRMED" in text:
                    confirmed = True
                    break
                if not pj.is_alive():
                    break
                time.sleep(0.1)
            if confirmed and pj.is_alive():
                print("sip: Meet answered — sending PIN", flush=True)
                time.sleep(4.5)
                self.send_dtmf(self.dtmf_after_confirm)
        # Ignore dial/setup log; only new DISCONNECTED means remote hangup.
        self._log_mark = len(pj.read_log())

    def is_alive(self) -> bool:
        return self._pj is not None and self._pj.is_alive()

    def remote_ended(self) -> bool:
        """True if the far end hung up (or pjsua died) while we are still off-hook."""
        pj = self._pj
        if pj is None or not pj.is_alive():
            return True
        text = pj.read_log()
        chunk = text[self._log_mark :]
        if "DISCONNECTED" in chunk:
            self._log_mark = len(text)
            return True
        return False

    def hangup(self) -> None:
        pj = self._pj
        self._pj = None
        if pj is not None:
            pj.hangup()
    def send_dtmf(self, digits: str) -> None:
        """Send RFC 2833 DTMF (Meet PIN, etc.) once the call is up."""
        pj = self._pj
        if pj is None or not pj.is_alive():
            return
        cleaned = re.sub(r"[^0-9*#]", "", digits or "")
        if not cleaned:
            return
        try:
            print(f"sip: DTMF {cleaned}", flush=True)
            # pjsua menu: `#` opens RFC 2833 prompt, then the digit string.
            pj.write(b"#\r")
            time.sleep(0.35)
            pj.write((cleaned + "\r").encode())
            # Give RFC 2833 time to flush before anything else touches the PTY.
            time.sleep(0.4 + 0.15 * len(cleaned))
        except OSError:
            pass

    def _dial(self) -> None:
        pj = self._pj
        if pj is None or not pj.is_alive():
            raise RuntimeError("pjsua exited before dial")
        dest = f"sip:{self.e164}@{self.credentials.registrar}"
        try:
            pj.write(b"m\r")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if "Make call" in pj.read_log():
                    break
                time.sleep(0.05)
            pj.write((dest + "\r").encode())
        except OSError as e:
            raise RuntimeError(f"failed to dial via pjsua: {e}") from e

    def _check_early_progress(self) -> None:
        pj = self._pj
        if pj is None:
            return
        deadline = time.monotonic() + self.progress_timeout_s
        while time.monotonic() < deadline:
            text = pj.read_log()
            err = _telnyx_reject_message(text)
            if err:
                self.hangup()
                raise RuntimeError(err)
            if any(
                s in text
                for s in (
                    "state changed to EARLY",
                    "state changed to CONFIRMED",
                    "SIP/2.0 180",
                    "SIP/2.0 183",
                    "SIP/2.0 200",
                )
            ):
                return
            if "DISCONNECTED" in text and "CALLING" in text:
                err = _telnyx_reject_message(text) or "SIP call disconnected immediately"
                self.hangup()
                raise RuntimeError(err)
            time.sleep(0.1)


@dataclass
class SipInboundListener:
    """Registered softphone that rings the desk set on inbound INVITE.

    Uses SIP username as ``--id`` (required for Telnyx REGISTER). Stop before
    outbound dial — both bind ``LOCAL_SIP_PORT``.

    Audio is always the virtual SIP line (snd-aloop). The USB handset is joined
    only by ``HandsetBridge`` when main starts a live (off-hook) call — never
    from voicemail / other on-hook features.
    """

    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    register_timeout_s: float = 12.0
    null_audio: bool = False
    greeting_wav: Path | None = None
    _pj: _PjsuaProc | None = field(default=None, init=False, repr=False)
    _phase: str = field(default="down", init=False, repr=False)  # down|listen|ringing|up
    _mark: int = field(default=0, init=False, repr=False)
    _remote_e164: str = field(default="", init=False, repr=False)

    def start(self) -> None:
        if self._pj is not None:
            return
        from operator_os.handset_bridge import ensure_loopback_card

        ensure_loopback_card()
        VM_DIR.mkdir(parents=True, exist_ok=True)
        try:
            ACTIVE_REC.unlink(missing_ok=True)
        except OSError:
            pass
        pj = _PjsuaProc(
            credentials=self.credentials,
            alsa_device=self.alsa_device,
            pjsua_path=self.pjsua_path,
            null_audio=self.null_audio,
        )
        cmd = [
            str(_resolve_pjsua(self.pjsua_path)),
            "--log-level=3",
            "--app-log-level=3",
            "--no-color",
            f"--id={self.credentials.register_id()}",
            # TCP so Telnyx can reuse the connection for inbound INVITEs behind NAT.
            f"--registrar=sip:{self.credentials.registrar};transport=tcp",
            "--realm=*",
            f"--username={self.credentials.username}",
            f"--password={self.credentials.password}",
            "--reg-timeout=180",
            # 180 Ringing (provisional) — without it the PSTN leg often dies after
            # one ring with only a 100 Trying.
            "--auto-answer=180",
            f"--local-port={LOCAL_SIP_PORT}",
            "--use-srtp=0",
            "--srtp-secure=0",
            # Recorder port exists but is NOT auto-connected — we arm it only
            # after the OGM so the mailbox WAV has no leading silence.
            f"--rec-file={ACTIVE_REC}",
        ]
        greet = self.greeting_wav
        if greet is not None and greet.is_file():
            cmd.append(f"--play-file={greet}")
        cmd.extend(_pjsua_media_args(null_audio=self.null_audio))
        pj.spawn(cmd)
        self._pj = pj
        if not pj.wait_log("registration success", self.register_timeout_s):
            detail = pj.log_snippet()
            self.hangup()
            raise RuntimeError(
                "SIP inbound registration failed"
                + (f": {detail}" if detail else "")
            )
        self._phase = "listen"
        self._mark = len(pj.read_log())
        self._remote_e164 = ""

    def is_alive(self) -> bool:
        return self._pj is not None and self._pj.is_alive()

    def poll(self) -> str | None:
        """Return ``incoming``, ``ended``, or None. Each event once."""
        pj = self._pj
        if pj is None:
            return None
        text = pj.read_log()
        chunk = text[self._mark :]
        if self._phase == "listen":
            # pjsua prints "Incoming call for account N!" (not always "INCOMING").
            if (
                "Incoming call" in chunk
                or "INCOMING" in chunk
                or "state changed to INCOMING" in chunk
            ):
                self._phase = "ringing"
                self._mark = len(text)
                self._remote_e164 = _cli_from_sip_log(text)
                return "incoming"
        elif self._phase in ("ringing", "up"):
            if "DISCONNECTED" in chunk:
                self._mark = len(text)
                self._phase = "listen"
                return "ended"
            if self._phase == "ringing" and "state changed to CONFIRMED" in chunk:
                self._phase = "up"
                self._mark = len(text)
        return None

    def remote_e164(self) -> str:
        if self._remote_e164:
            return self._remote_e164
        pj = self._pj
        if pj is not None:
            self._remote_e164 = _cli_from_sip_log(pj.read_log())
        return self._remote_e164

    def _conf_ports(self) -> dict[str, int]:
        pj = self._pj
        if pj is None:
            return {}
        pj.write(b"cl\r")
        time.sleep(0.2)
        return _parse_conf_ports(pj.read_log())

    def _pause_recorder(self) -> None:
        """Disconnect call → recorder if connected."""
        pj = self._pj
        if pj is None or not pj.is_alive():
            return
        ports = self._conf_ports()
        call = ports.get("call")
        rec = ports.get("recorder")
        if call is None or rec is None:
            return
        pj.write(f"cd\r{call}\r{rec}\r".encode())
        time.sleep(0.05)

    def _greeting_duration_s(self) -> float:
        """Actual play-through length of the on-disk OGM (includes trailing beep)."""
        path = self.greeting_wav if self.greeting_wav is not None else VM_GREETING_WAV
        if not path.is_file():
            raise RuntimeError(f"voicemail greeting missing: {path}")
        dur = wav_duration_s(path)
        if dur <= 0:
            raise RuntimeError(f"voicemail greeting has no duration: {path}")
        return dur

    def _arm_recorder(self) -> None:
        """Connect call → recorder (message window only — after the beep)."""
        pj = self._pj
        if pj is None or not pj.is_alive():
            return
        ports = self._conf_ports()
        call = ports.get("call")
        rec = ports.get("recorder")
        if call is None or rec is None:
            return
        pj.write(f"cc\r{call}\r{rec}\r".encode())
        time.sleep(0.05)

    def _play_conference_greeting_once(self) -> None:
        """Play OGM on the SIP leg, then arm the recorder for the caller.

        Wait time is the measured WAV duration (re-read from disk), not a
        hardcoded constant — so a longer/shorter greeting stays in sync.
        ``--rec-file`` is registered without ``--auto-rec``, so nothing is
        written until ``_arm_recorder`` after the beep.
        """
        pj = self._pj
        if pj is None or not pj.is_alive():
            return
        try:
            dur = self._greeting_duration_s()
        except RuntimeError as e:
            # Fail soft: skip OGM rather than invent a sleep length.
            print(f"vm: {e}", flush=True)
            self._arm_recorder()
            return
        ports = self._conf_ports()
        call = ports.get("call")
        wav = ports.get("file")
        if call is None or wav is None:
            self._arm_recorder()
            return
        pj.write(f"cc\r{wav}\r{call}\r".encode())
        time.sleep(0.05)
        # Full file length; brief settle for the last samples to leave the bridge.
        time.sleep(dur + 0.05)
        pj.write(f"cd\r{wav}\r{call}\r".encode())
        time.sleep(0.05)
        self._arm_recorder()

    def take_recording(self, dest: Path) -> Path | None:
        return take_active_recording(dest)

    def discard_recording(self) -> None:
        discard_active_recording()

    def answer(self, *, handset: bool = True) -> None:
        """Answer the inbound INVITE on the virtual SIP line.

        Softphone audio is always snd-aloop — never the USB handset.
        ``handset=True`` means the caller already lifted; main must start
        ``HandsetBridge`` so alsaloop joins line ↔ cradle. Voicemail uses
        ``handset=False``: conference greeting/record only; bridge stays down.
        """
        pj = self._pj
        if pj is None or not pj.is_alive():
            raise RuntimeError("inbound pjsua not running")
        pj.write(b"a\r200\r")
        self._phase = "up"
        self._mark = len(pj.read_log())
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            text = pj.read_log()
            if "CONFIRMED" in text or "state changed to CONFIRMED" in text:
                break
            if "DISCONNECTED" in text:
                raise RuntimeError("SIP answer failed (call dropped)")
            time.sleep(0.05)
        else:
            if "CONFIRMED" not in pj.read_log():
                raise RuntimeError("SIP answer sent but call not confirmed")

        if not handset:
            # On-hook miss: OGM + record on the virtual line only.
            self._play_conference_greeting_once()

    def hangup(self) -> None:
        pj = self._pj
        self._pj = None
        self._phase = "down"
        self._mark = 0
        if pj is not None:
            pj.hangup()


def take_active_recording(dest: Path) -> Path | None:
    """Move conference recording to dest if present and non-empty."""
    if not ACTIVE_REC.is_file() or ACTIVE_REC.stat().st_size < 44:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        ACTIVE_REC.replace(dest)
    except OSError:
        return None
    return dest if dest.is_file() else None


def discard_active_recording() -> None:
    try:
        ACTIVE_REC.unlink(missing_ok=True)
    except OSError:
        pass


def wav_duration_s(path: Path) -> float:
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            rate = float(w.getframerate() or 0)
            if rate <= 0:
                return 0.0
            return w.getnframes() / rate
    except (OSError, wave.Error):
        return 0.0
