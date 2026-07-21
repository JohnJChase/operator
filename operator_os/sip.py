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


def _write_asoundrc(home: Path, device: str) -> None:
    """Force pjsua's default PCM to the handset ALSA device."""
    card = "0"
    m = re.match(r"(?:plug)?hw:(\d+)", device.replace(" ", ""))
    if m:
        card = m.group(1)
    (home / ".asoundrc").write_text(
        "\n".join(
            [
                "pcm.!default {",
                "  type plug",
                f'  slave.pcm "{device}"',
                "}",
                "ctl.!default {",
                "  type hw",
                f"  card {card}",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )


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
    """Shared media flags for handset acoustics (carbon mic + receiver)."""
    args = [
        "--clock-rate=8000",
        "--snd-clock-rate=8000",
        # WebRTC AEC3; long tail for handset acoustic coupling.
        "--ec-opt=4",
        "--ec-tail=800",
        "--capture-lat=40",
        "--playback-lat=40",
        "--capture-dev=0",
        "--playback-dev=0",
        "--no-vad",
        "--dis-codec=speex",
        "--dis-codec=ilbc",
        "--add-codec=pcmu",
        "--add-codec=pcma",
    ]
    if null_audio:
        args.insert(0, "--null-audio")
    return args


def sip_configured() -> bool:
    creds = SipCredentials.from_env()
    return creds is not None and bool(creds.caller_id.strip())


@dataclass
class _PjsuaProc:
    """Shared pjsua console process (PTY + log drain)."""

    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    null_audio: bool = False
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
            _write_asoundrc(self._home, self.alsa_device)
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
    """

    e164: str
    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    progress_timeout_s: float = 5.0
    null_audio: bool = False
    _pj: _PjsuaProc | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if not self.credentials.caller_id.strip():
            raise RuntimeError(
                "TELNYX_CALLER_ID is required (Telnyx number for outbound caller ID)"
            )
        pj = _PjsuaProc(
            credentials=self.credentials,
            alsa_device=self.alsa_device,
            pjsua_path=self.pjsua_path,
            null_audio=self.null_audio,
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
                self.hangup()
                raise RuntimeError(
                    "pjsua exited at startup" + (f": {detail}" if detail else "")
                )
        time.sleep(0.3)
        self._dial()
        self._check_early_progress()

    def is_alive(self) -> bool:
        return self._pj is not None and self._pj.is_alive()

    def hangup(self) -> None:
        pj = self._pj
        self._pj = None
        if pj is not None:
            pj.hangup()

    def remote_ended(self) -> bool:
        pj = self._pj
        if pj is None:
            return True
        return "DISCONNECTED" in pj.read_log()

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
    """

    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    register_timeout_s: float = 12.0
    null_audio: bool = False
    _pj: _PjsuaProc | None = field(default=None, init=False, repr=False)
    _phase: str = field(default="down", init=False, repr=False)  # down|listen|ringing|up
    _mark: int = field(default=0, init=False, repr=False)

    def start(self) -> None:
        if self._pj is not None:
            return
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
            *_pjsua_media_args(null_audio=self.null_audio),
        ]
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

    def answer(self) -> None:
        pj = self._pj
        if pj is None or not pj.is_alive():
            raise RuntimeError("inbound pjsua not running")
        # CLI prompts: "Answer with code (100-699)" — bare `a` is not enough.
        pj.write(b"a\r200\r")
        self._phase = "up"
        self._mark = len(pj.read_log())
        # Wait briefly for 200/CONFIRMED so we fail loud if answer didn't take.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            text = pj.read_log()
            if "CONFIRMED" in text or "state changed to CONFIRMED" in text:
                return
            if "DISCONNECTED" in text:
                raise RuntimeError("SIP answer failed (call dropped)")
            time.sleep(0.05)
        # Soft warning only — some builds log differently.
        if "CONFIRMED" not in pj.read_log():
            raise RuntimeError("SIP answer sent but call not confirmed")

    def hangup(self) -> None:
        pj = self._pj
        self._pj = None
        self._phase = "down"
        self._mark = 0
        if pj is not None:
            pj.hangup()
