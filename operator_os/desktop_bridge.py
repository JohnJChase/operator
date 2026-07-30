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

# Product routing keys (wire protocol stays desktop.* / open_url / notify).
INTENT_OPEN_URL = "open.url"
INTENT_OPEN_MEETING = "open.meeting"
INTENT_NOTIFY_MESSAGES = "notify.messages"

_ROUTE_FANOUT = "fanout"
_ROUTE_UNICAST = "unicast"


def desktop_token() -> str:
    return os.environ.get("OPERATOR_DESKTOP_TOKEN", "").strip()


def desktop_client_target() -> str:
    """Preferred client for unicast opens (Meet/URL). Ignored for notify fan-out."""
    return _clean_id(os.environ.get("OPERATOR_DESKTOP_CLIENT_ID", "").strip())


def meet_join_target() -> str:
    raw = os.environ.get("OPERATOR_MEET_JOIN_TARGET", "phone").strip().lower()
    return raw if raw in {"phone", "desktop", "auto"} else "phone"


def _route_mode_for(command_type: str) -> str:
    return _ROUTE_FANOUT if command_type == "desktop.notify" else _ROUTE_UNICAST


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


def meeting_title(meeting: Any) -> str:
    title = " ".join(str(getattr(meeting, "title", "") or "").split()) or "the meeting"
    if len(title) > 48:
        title = title[:45].rstrip() + "..."
    return title


def sms_notification_payload(
    *,
    message_id: int,
    from_e164: str,
    body: str,
    from_name: str | None = None,
) -> dict[str, Any]:
    sender = (from_name or from_e164 or "Unknown").strip()
    preview = " ".join((body or "").split())
    if len(preview) > 220:
        preview = preview[:217].rstrip() + "..."
    return {
        "title": f"Message from {sender}"[:80],
        "body": preview,
        "message_id": int(message_id),
        "from_e164": from_e164,
    }


@dataclass(frozen=True)
class DesktopDelivery:
    ok: bool
    reason: str = ""
    command: dict[str, Any] | None = None


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
    _conn_gen: dict[str, int] = field(default_factory=dict, init=False)
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

    def connect(self, client_id: str) -> tuple[dict[str, Any], int]:
        """Mark online and return (public client dict, connection generation).

        Generation lets a superseded SSE stream disconnect without marking the
        newer stream offline (Mac app relaunch race).
        """
        cid = _clean_id(client_id)
        with self._lock:
            client = self._require_client(cid)
            now = time.monotonic()
            gen = self._conn_gen.get(cid, 0) + 1
            self._conn_gen[cid] = gen
            self._clients[cid] = DesktopClient(
                client_id=client.client_id,
                name=client.name,
                capabilities=client.capabilities,
                last_seen=now,
                connected=True,
                last_ack=client.last_ack,
            )
            return self._clients[cid].public_dict(now=now), gen

    def disconnect(self, client_id: str, generation: int | None = None) -> None:
        cid = _clean_id(client_id)
        with self._lock:
            client = self._clients.get(cid)
            if client is None:
                return
            if generation is not None and self._conn_gen.get(cid) != generation:
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
        route: str | None = None,
    ) -> dict[str, Any] | None:
        if command_type not in DESKTOP_COMMAND_TYPES:
            raise ValueError(f"unsupported desktop command: {command_type}")
        mode = route or _route_mode_for(command_type)
        if mode not in {_ROUTE_FANOUT, _ROUTE_UNICAST}:
            raise ValueError(f"unsupported route mode: {mode}")
        clean_payload = dict(payload)
        if command_type == "desktop.open_url":
            clean_payload["url"] = validate_open_url(str(clean_payload.get("url") or ""))
        cap = _capability_for(command_type)
        targets = self._select_targets(capability=cap, target_id=target_id, mode=mode)
        if not targets:
            return None
        created_at = time.time()
        first: dict[str, Any] | None = None
        for cid in targets:
            cmd = {
                "id": secrets.token_urlsafe(12),
                "type": command_type,
                "payload": clean_payload,
                "created_at": created_at,
            }
            q = self._queues.setdefault(cid, queue.Queue(maxsize=100))
            _put_drop_oldest(q, cmd)
            if first is None:
                first = dict(cmd)
        return first

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

    def _online_capable(self, *, capability: str) -> list[str]:
        """Capable online clients, sorted by client_id for stable unicast fallback."""
        now = time.monotonic()
        out: list[str] = []
        with self._lock:
            for client in self._clients.values():
                if capability not in client.capabilities:
                    continue
                if not client.connected or (now - client.last_seen) > DESKTOP_TTL_S:
                    continue
                out.append(client.client_id)
        out.sort()
        return out

    def _select_targets(
        self, *, capability: str, target_id: str = "", mode: str = _ROUTE_UNICAST
    ) -> list[str]:
        online = self._online_capable(capability=capability)
        if not online:
            return []
        tid = _clean_id(target_id)
        if mode == _ROUTE_FANOUT:
            if tid:
                return [tid] if tid in online else []
            return online
        # Unicast: preferred if online, else first capable online (sorted id).
        if tid and tid in online:
            return [tid]
        if tid:
            # Preferred set but offline/missing — fall through to first online.
            return [online[0]]
        return [online[0]]

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


@dataclass
class DesktopBridge:
    """Pi-side desktop boundary.

    Feature code should call the named intent methods here. Raw command queueing
    stays available for HTTP diagnostics, but normal product flows should not
    build ``desktop.*`` payloads themselves.
    """

    registry: DesktopRegistry = field(default_factory=DesktopRegistry)

    def register_client(
        self, client_id: str, name: str, capabilities: list[str]
    ) -> dict[str, Any]:
        return self.registry.register(client_id, name, capabilities)

    def connect_client(self, client_id: str) -> tuple[dict[str, Any], int]:
        return self.registry.connect(client_id)

    def disconnect_client(self, client_id: str, generation: int | None = None) -> None:
        self.registry.disconnect(client_id, generation=generation)

    def next_command(
        self, client_id: str, *, timeout_s: float = 15.0
    ) -> dict[str, Any] | None:
        return self.registry.next_command(client_id, timeout_s=timeout_s)

    def ack_command(
        self, client_id: str, command_id: str, status: str, message: str = ""
    ) -> None:
        self.registry.ack(client_id, command_id, status, message)

    def clients(self) -> list[dict[str, Any]]:
        return self.registry.clients()

    def client_summary(self) -> str:
        clients = self.clients()
        if not clients:
            return "none"
        parts = []
        for client in clients:
            state = "online" if client.get("online") else "offline"
            caps = ",".join(client.get("capabilities") or []) or "-"
            parts.append(f"{client.get('client_id')}:{state}:{caps}")
        return "; ".join(parts)

    def has_client(self, *, capability: str | None = None, target_id: str = "") -> bool:
        """True if any capable online client exists (preferred is delivery-only)."""
        return self.registry.has_online_client(
            capability=capability,
            target_id=_clean_id(target_id),
        )

    def open_url(self, *, url: str, title: str = "", target_id: str = "") -> DesktopDelivery:
        return self._queue(
            "desktop.open_url",
            {"url": url, "title": str(title or "")[:120]},
            target_id=target_id,
        )

    def open_meeting(self, meeting: Any, *, target_id: str = "") -> DesktopDelivery:
        url = meet_video_url(meeting)
        if not url:
            return DesktopDelivery(False, reason="no_meet_url")
        return self._queue(
            "desktop.open_url",
            {"url": url, "title": meeting_title(meeting)},
            target_id=target_id,
        )

    def notify(
        self,
        *,
        title: str,
        body: str,
        extra: dict[str, Any] | None = None,
        target_id: str = "",
    ) -> DesktopDelivery:
        payload = dict(extra or {})
        payload["title"] = str(title or "Operator")[:80]
        payload["body"] = str(body or "")[:240]
        return self._queue("desktop.notify", payload, target_id=target_id)

    def notify_inbound_sms(
        self,
        *,
        message_id: int,
        from_e164: str,
        body: str,
        from_name: str | None = None,
        target_id: str = "",
    ) -> DesktopDelivery:
        payload = sms_notification_payload(
            message_id=message_id,
            from_e164=from_e164,
            from_name=from_name,
            body=body,
        )
        return self._queue("desktop.notify", payload, target_id=target_id)

    def queue_raw(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        target_id: str = "",
    ) -> DesktopDelivery:
        return self._queue(command_type, payload, target_id=target_id)

    def _queue(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        target_id: str = "",
    ) -> DesktopDelivery:
        mode = _route_mode_for(command_type)
        # Notify fan-out ignores OPERATOR_DESKTOP_CLIENT_ID; only an explicit
        # target_id (diagnostics) narrows delivery. Unicast opens use preferred.
        if mode == _ROUTE_UNICAST:
            effective_target = _clean_id(target_id) or desktop_client_target()
        else:
            effective_target = _clean_id(target_id)
        try:
            cmd = self.registry.queue_command(
                command_type,
                payload,
                target_id=effective_target,
                route=mode,
            )
        except ValueError as e:
            return DesktopDelivery(False, reason=str(e))
        if cmd is None:
            return DesktopDelivery(False, reason="no_client")
        return DesktopDelivery(True, command=cmd)
