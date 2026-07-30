"""Desktop companion bridge: Pi queues intents, Mac executes a small allowlist."""

from __future__ import annotations

import os
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


DESKTOP_TTL_S = 45.0
DESKTOP_COMMAND_TYPES = {"desktop.open_url", "desktop.notify"}


def desktop_token() -> str:
    return os.environ.get("OPERATOR_DESKTOP_TOKEN", "").strip()


def desktop_client_target() -> str:
    return _clean_id(os.environ.get("OPERATOR_DESKTOP_CLIENT_ID", "").strip())


def meet_join_target() -> str:
    raw = os.environ.get("OPERATOR_MEET_JOIN_TARGET", "phone").strip().lower()
    return raw if raw in {"phone", "desktop", "auto"} else "phone"


def validate_open_url(url: str) -> str:
    got = (url or "").strip()
    parsed = urlparse(got)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be http(s)")
    if parsed.username or parsed.password:
        raise ValueError("url must not include credentials")
    return got


def meet_video_url(meeting: Any) -> str:
    cid = str(getattr(meeting, "conference_id", "") or "").strip().lower()
    if not cid or not re.fullmatch(r"[a-z0-9-]{5,80}", cid):
        return ""
    return f"https://meet.google.com/{cid}"


@dataclass(frozen=True)
class DesktopClient:
    client_id: str
    name: str
    capabilities: tuple[str, ...]
    last_seen: float
    connected: bool = False
    last_ack: dict[str, Any] | None = None

    def public_dict(self, *, now: float | None = None) -> dict[str, Any]:
        t = time.monotonic() if now is None else now
        online = self.connected and (t - self.last_seen) <= DESKTOP_TTL_S
        return {
            "client_id": self.client_id,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "online": online,
            "connected": self.connected,
            "last_seen_age_s": round(max(0.0, t - self.last_seen), 1),
            "last_ack": self.last_ack,
        }


@dataclass
class DesktopRegistry:
    _clients: dict[str, DesktopClient] = field(default_factory=dict, init=False)
    _queues: dict[str, queue.Queue[dict[str, Any]]] = field(default_factory=dict, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def register(self, client_id: str, name: str, capabilities: list[str]) -> dict[str, Any]:
        cid = _clean_id(client_id)
        if not cid:
            raise ValueError("client_id required")
        caps = tuple(_clean_cap(c) for c in capabilities if _clean_cap(c))
        now = time.monotonic()
        with self._lock:
            prev = self._clients.get(cid)
            self._clients[cid] = DesktopClient(
                client_id=cid,
                name=(name or cid).strip()[:80],
                capabilities=caps,
                last_seen=now,
                connected=prev.connected if prev else False,
                last_ack=prev.last_ack if prev else None,
            )
            self._queues.setdefault(cid, queue.Queue(maxsize=100))
            return self._clients[cid].public_dict(now=now)

    def connect(self, client_id: str) -> dict[str, Any]:
        cid = _clean_id(client_id)
        with self._lock:
            client = self._require_client(cid)
            now = time.monotonic()
            self._clients[cid] = DesktopClient(
                client_id=client.client_id,
                name=client.name,
                capabilities=client.capabilities,
                last_seen=now,
                connected=True,
                last_ack=client.last_ack,
            )
            return self._clients[cid].public_dict(now=now)

    def disconnect(self, client_id: str) -> None:
        cid = _clean_id(client_id)
        with self._lock:
            client = self._clients.get(cid)
            if client is None:
                return
            self._clients[cid] = DesktopClient(
                client_id=client.client_id,
                name=client.name,
                capabilities=client.capabilities,
                last_seen=time.monotonic(),
                connected=False,
                last_ack=client.last_ack,
            )

    def touch(self, client_id: str) -> None:
        cid = _clean_id(client_id)
        with self._lock:
            client = self._clients.get(cid)
            if client is None:
                return
            self._clients[cid] = DesktopClient(
                client_id=client.client_id,
                name=client.name,
                capabilities=client.capabilities,
                last_seen=time.monotonic(),
                connected=client.connected,
                last_ack=client.last_ack,
            )

    def clients(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            return [
                c.public_dict(now=now)
                for c in sorted(self._clients.values(), key=lambda c: c.client_id)
            ]

    def has_online_client(self, *, capability: str | None = None, target_id: str = "") -> bool:
        now = time.monotonic()
        with self._lock:
            for client in self._clients.values():
                if target_id and client.client_id != target_id:
                    continue
                if capability and capability not in client.capabilities:
                    continue
                if client.connected and (now - client.last_seen) <= DESKTOP_TTL_S:
                    return True
        return False

    def queue_command(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        target_id: str = "",
    ) -> dict[str, Any] | None:
        if command_type not in DESKTOP_COMMAND_TYPES:
            raise ValueError(f"unsupported desktop command: {command_type}")
        clean_payload = dict(payload)
        if command_type == "desktop.open_url":
            clean_payload["url"] = validate_open_url(str(clean_payload.get("url") or ""))
        cap = _capability_for(command_type)
        targets = self._target_clients(capability=cap, target_id=target_id)
        if not targets:
            return None
        cmd = {
            "id": secrets.token_urlsafe(12),
            "type": command_type,
            "payload": clean_payload,
            "created_at": time.time(),
        }
        for cid in targets:
            q = self._queues.setdefault(cid, queue.Queue(maxsize=100))
            _put_drop_oldest(q, cmd)
        return dict(cmd)

    def next_command(self, client_id: str, *, timeout_s: float = 15.0) -> dict[str, Any] | None:
        cid = _clean_id(client_id)
        with self._lock:
            q = self._queues.setdefault(cid, queue.Queue(maxsize=100))
        self.touch(cid)
        try:
            return q.get(timeout=timeout_s)
        except queue.Empty:
            self.touch(cid)
            return None

    def ack(self, client_id: str, command_id: str, status: str, message: str = "") -> None:
        cid = _clean_id(client_id)
        with self._lock:
            client = self._clients.get(cid)
            if client is None:
                return
            self._clients[cid] = DesktopClient(
                client_id=client.client_id,
                name=client.name,
                capabilities=client.capabilities,
                last_seen=time.monotonic(),
                connected=client.connected,
                last_ack={
                    "command_id": str(command_id or "")[:80],
                    "status": str(status or "")[:24],
                    "message": str(message or "")[:160],
                    "at": time.time(),
                },
            )

    def _target_clients(self, *, capability: str, target_id: str = "") -> list[str]:
        now = time.monotonic()
        out: list[str] = []
        tid = _clean_id(target_id)
        with self._lock:
            for client in self._clients.values():
                if tid and client.client_id != tid:
                    continue
                if capability not in client.capabilities:
                    continue
                if not client.connected or (now - client.last_seen) > DESKTOP_TTL_S:
                    continue
                out.append(client.client_id)
        return out

    def _require_client(self, client_id: str) -> DesktopClient:
        client = self._clients.get(client_id)
        if client is None:
            raise ValueError("client is not registered")
        return client


def _capability_for(command_type: str) -> str:
    return command_type.removeprefix("desktop.")


def _clean_id(raw: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw or "").strip())
    return s.strip("-._")[:64]


def _clean_cap(raw: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "", str(raw or "").strip().lower())[:40]


def _put_drop_oldest(q: queue.Queue[dict[str, Any]], item: dict[str, Any]) -> None:
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    q.put_nowait(item)
