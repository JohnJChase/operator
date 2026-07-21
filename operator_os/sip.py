"""Outside-line number helpers and Telnyx/pjsua SIP call session."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PJSUA = ROOT / "tools" / "pjsua"
TELNYX_SIP_HOST = "sip.telnyx.com"


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
    caller_id: str = ""  # E.164 for From display; optional

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


@dataclass
class SipCallSession:
    """One outbound Telnyx call via the bundled ``pjsua`` softphone."""

    e164: str
    credentials: SipCredentials
    alsa_device: str = "plughw:2,0"
    pjsua_path: Path = field(default_factory=lambda: DEFAULT_PJSUA)
    _proc: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _home: Path | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            exe = self._resolve_pjsua()
            self._home = Path(tempfile.mkdtemp(prefix="operator-sip-"))
            self._write_asoundrc(self._home, self.alsa_device)
            dest = f"sip:{self.e164}@{self.credentials.registrar}"
            local_id = f"sip:{self.credentials.username}@{self.credentials.registrar}"
            cmd = [
                str(exe),
                "--log-level=3",
                "--app-log-level=2",
                "--no-color",
                "--no-stderr",
                f"--id={local_id}",
                f"--registrar=sip:{self.credentials.registrar}",
                "--realm=*",
                f"--username={self.credentials.username}",
                f"--password={self.credentials.password}",
                "--reg-timeout=300",
                "--use-srtp=0",
                "--srtp-secure=0",
                "--no-vad",
                "--dis-codec=speex",
                "--dis-codec=ilbc",
                "--add-codec=pcmu",
                "--add-codec=pcma",
                dest,
            ]
            env = os.environ.copy()
            env["HOME"] = str(self._home)
            log_path = self._home / "pjsua.log"
            cmd.extend([f"--log-file={log_path}"])
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
            )

    def is_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def hangup(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            home = self._home
            self._home = None
        if proc is None:
            if home is not None:
                shutil.rmtree(home, ignore_errors=True)
            return
        try:
            if proc.stdin is not None and proc.poll() is None:
                # pjsua CLI: 'h' hangup, 'q' quit
                proc.stdin.write("h\nq\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)

    def _resolve_pjsua(self) -> Path:
        path = self.pjsua_path
        if path.is_file() and os.access(path, os.X_OK):
            return path
        which = shutil.which("pjsua")
        if which:
            return Path(which)
        raise FileNotFoundError(
            f"pjsua not found at {path}; build tools/pjsua (see docs/sip-outside-line.md)"
        )

    @staticmethod
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


def sip_configured() -> bool:
    return SipCredentials.from_env() is not None
