"""Desktop companion bridge: Pi queues intents, Mac executes a small allowlist."""

from __future__ import annotations

import os
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from operator_os.route_priority import (
    INTENT_NOTIFY as INTENT_NOTIFY_MESSAGES,
    INTENT_OPEN_MEETING,
    INTENT_OPEN_URL,
    WE302_MEET_ID,
    ensure_station_in_meeting_priority,
    load_priorities,
    save_priorities,
)


DESKTOP_TTL_S = 45.0
DESKTOP_COMMAND_TYPES = {"desktop.open_url", "desktop.notify"}
ACCEPT_ACK_STATUSES = frozenset({"accept", "ok"})
DEFAULT_ACCEPT_TIMEOUT_S = 2.5


class DesktopStreamSuperseded(Exception):
    """This SSE connection is no longer the active one for the client."""


_ROUTE_FANOUT = "fanout"
_ROUTE_UNICAST = "unicast"
_ROUTE_FAILOVER = "failover"


def desktop_token() -> str:
    return os.environ.get("OPERATOR_DESKTOP_TOKEN", "").strip()


def desktop_client_target() -> str:
    """Preferred client for unicast opens (Meet/URL). Ignored for notify fan-out."""
    return _clean_id(os.environ.get("OPERATOR_DESKTOP_CLIENT_ID", "").strip())


def meet_join_target() -> str:
    raw = os.environ.get("OPERATOR_MEET_JOIN_TARGET", "phone").strip().lower()
    return raw if raw in {"phone", "desktop", "auto"} else "phone"


def accept_timeout_s() -> float:
    raw = os.environ.get("OPERATOR_DESKTOP_ACCEPT_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_ACCEPT_TIMEOUT_S
    try:
        return max(0.5, min(15.0, float(raw)))
    except ValueError:
        return DEFAULT_ACCEPT_TIMEOUT_S


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
    handler: str = ""


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
            "kind": "desktop",
        }


@dataclass(frozen=True)
class LocalStation:
    """Plant-side station that participates in failover without SSE."""

    client_id: str
    name: str
    capabilities: tuple[str, ...]
    eligible: Callable[[], bool] = field(default=lambda: True)

    def public_dict(self) -> dict[str, Any]:
        online = bool(self.eligible())
        return {
            "client_id": self.client_id,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "online": online,
            "connected": online,
            "last_seen_age_s": 0.0,
            "last_ack": None,
            "kind": "local",
        }


@dataclass
class DesktopRegistry:
    _clients: dict[str, DesktopClient] = field(default_factory=dict, init=False)
    _queues: dict[str, queue.Queue[dict[str, Any]]] = field(default_factory=dict, init=False)
    _conn_gen: dict[str, int] = field(default_factory=dict, init=False)
    _ack_waiters: dict[str, queue.Queue[str]] = field(default_factory=dict, init=False)
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
        clean_payload = self._clean_payload(command_type, payload)
        cap = _capability_for(command_type)
        targets = self._select_targets(capability=cap, target_id=target_id, mode=mode)
        if not targets:
            return None
        created_at = time.time()
        first: dict[str, Any] | None = None
        for cid in targets:
            cmd = self._make_command(command_type, clean_payload, created_at=created_at)
            q = self._queues.setdefault(cid, queue.Queue(maxsize=100))
            _put_drop_oldest(q, cmd)
            if first is None:
                first = dict(cmd)
        return first

    def enqueue_one(
        self, client_id: str, command_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Queue a command to exactly one client (failover hop)."""
        if command_type not in DESKTOP_COMMAND_TYPES:
            raise ValueError(f"unsupported desktop command: {command_type}")
        cid = _clean_id(client_id)
        if not cid:
            raise ValueError("client_id required")
        clean_payload = self._clean_payload(command_type, payload)
        cmd = self._make_command(command_type, clean_payload)
        with self._lock:
            if cid not in self._clients:
                raise ValueError("client is not registered")
            q = self._queues.setdefault(cid, queue.Queue(maxsize=100))
        _put_drop_oldest(q, cmd)
        return dict(cmd)

    def wait_ack(self, command_id: str, timeout_s: float) -> str:
        """Block until ack for command_id, or return ``timeout``."""
        cid = str(command_id or "").strip()
        if not cid:
            return "timeout"
        waiter: queue.Queue[str] = queue.Queue(maxsize=1)
        with self._lock:
            self._ack_waiters[cid] = waiter
        try:
            return waiter.get(timeout=max(0.0, timeout_s))
        except queue.Empty:
            return "timeout"
        finally:
            with self._lock:
                self._ack_waiters.pop(cid, None)

    @staticmethod
    def _clean_payload(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_payload = dict(payload)
        if command_type == "desktop.open_url":
            clean_payload["url"] = validate_open_url(str(clean_payload.get("url") or ""))
        return clean_payload

    @staticmethod
    def _make_command(
        command_type: str, payload: dict[str, Any], *, created_at: float | None = None
    ) -> dict[str, Any]:
        return {
            "id": secrets.token_urlsafe(12),
            "type": command_type,
            "payload": payload,
            "created_at": time.time() if created_at is None else created_at,
        }

    def next_command(
        self,
        client_id: str,
        *,
        timeout_s: float = 15.0,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Wait for the next queued command.

        When ``generation`` is set, return only while that SSE stream is still
        current. A superseded waiter exits without consuming the queue so a
        zombie connection (Mac relaunch) cannot steal notifies from the live
        stream.
        """
        cid = _clean_id(client_id)
        with self._lock:
            q = self._queues.setdefault(cid, queue.Queue(maxsize=100))
        self.touch(cid)
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            if generation is not None:
                with self._lock:
                    if self._conn_gen.get(cid) != generation:
                        raise DesktopStreamSuperseded
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.touch(cid)
                return None
            try:
                return q.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue

    def ack(self, client_id: str, command_id: str, status: str, message: str = "") -> None:
        cid = _clean_id(client_id)
        cmd_id = str(command_id or "")[:80]
        status_s = str(status or "")[:24]
        with self._lock:
            client = self._clients.get(cid)
            if client is None:
                waiter = self._ack_waiters.get(cmd_id)
            else:
                self._clients[cid] = DesktopClient(
                    client_id=client.client_id,
                    name=client.name,
                    capabilities=client.capabilities,
                    last_seen=time.monotonic(),
                    connected=client.connected,
                    last_ack={
                        "command_id": cmd_id,
                        "status": status_s,
                        "message": str(message or "")[:160],
                        "at": time.time(),
                    },
                )
                waiter = self._ack_waiters.get(cmd_id)
        if waiter is not None and cmd_id:
            try:
                waiter.put_nowait(status_s)
            except queue.Full:
                pass

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
    _locals: dict[str, LocalStation] = field(default_factory=dict, init=False)
    _priorities: dict[str, list[str]] = field(default_factory=load_priorities, init=False)
    _prio_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def register_local(
        self,
        client_id: str,
        name: str,
        capabilities: list[str],
        *,
        eligible: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        cid = _clean_id(client_id)
        if not cid:
            raise ValueError("client_id required")
        caps = tuple(_clean_cap(c) for c in capabilities if _clean_cap(c))
        station = LocalStation(
            client_id=cid,
            name=(name or cid).strip()[:80],
            capabilities=caps,
            eligible=eligible or (lambda: True),
        )
        self._locals[cid] = station
        return station.public_dict()

    def register_client(
        self, client_id: str, name: str, capabilities: list[str]
    ) -> dict[str, Any]:
        client = self.registry.register(client_id, name, capabilities)
        caps = set(client.get("capabilities") or [])
        if "open_url" in caps:
            with self._prio_lock:
                if ensure_station_in_meeting_priority(client["client_id"], self._priorities):
                    try:
                        save_priorities(self._priorities)
                    except OSError:
                        pass
        return client

    def connect_client(self, client_id: str) -> tuple[dict[str, Any], int]:
        return self.registry.connect(client_id)

    def disconnect_client(self, client_id: str, generation: int | None = None) -> None:
        self.registry.disconnect(client_id, generation=generation)

    def next_command(
        self,
        client_id: str,
        *,
        timeout_s: float = 15.0,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        return self.registry.next_command(
            client_id, timeout_s=timeout_s, generation=generation
        )

    def ack_command(
        self, client_id: str, command_id: str, status: str, message: str = ""
    ) -> None:
        self.registry.ack(client_id, command_id, status, message)

    def clients(self) -> list[dict[str, Any]]:
        remotes = self.registry.clients()
        locals_ = [s.public_dict() for s in sorted(self._locals.values(), key=lambda s: s.client_id)]
        return remotes + locals_

    def client_summary(self) -> str:
        clients = self.clients()
        if not clients:
            return "none"
        parts = []
        for client in clients:
            state = "online" if client.get("online") else "offline"
            caps = ",".join(client.get("capabilities") or []) or "-"
            kind = client.get("kind") or "desktop"
            parts.append(f"{client.get('client_id')}:{state}:{caps}:{kind}")
        return "; ".join(parts)

    def has_client(self, *, capability: str | None = None, target_id: str = "") -> bool:
        """True if any capable online desktop client exists (locals excluded)."""
        return self.registry.has_online_client(
            capability=capability,
            target_id=_clean_id(target_id),
        )

    def has_meeting_route(self, *, mode: str | None = None) -> bool:
        join = mode or meet_join_target()
        return bool(self._meeting_candidates(mode=join))

    def get_priorities(self) -> dict[str, list[str]]:
        with self._prio_lock:
            return {k: list(v) for k, v in self._priorities.items()}

    def set_priorities(self, priorities: dict[str, Any]) -> dict[str, list[str]]:
        saved = save_priorities(priorities)
        with self._prio_lock:
            self._priorities = {k: list(v) for k, v in saved.items()}
            return {k: list(v) for k, v in self._priorities.items()}

    def routing_snapshot(self) -> dict[str, Any]:
        return {
            "priorities": self.get_priorities(),
            "stations": self.clients(),
            "policies": {
                INTENT_OPEN_MEETING: _ROUTE_FAILOVER,
                INTENT_OPEN_URL: _ROUTE_UNICAST,
                INTENT_NOTIFY_MESSAGES: _ROUTE_FANOUT,
            },
            "meet_join_target": meet_join_target(),
            "accept_timeout_s": accept_timeout_s(),
        }

    def open_url(self, *, url: str, title: str = "", target_id: str = "") -> DesktopDelivery:
        return self._queue(
            "desktop.open_url",
            {"url": url, "title": str(title or "")[:120]},
            target_id=target_id,
        )

    def open_meeting(
        self,
        meeting: Any,
        *,
        mode: str | None = None,
        target_id: str = "",
        accept_timeout: float | None = None,
    ) -> DesktopDelivery:
        """Failover open.meeting: try priority order until accept or local SIP.

        ``mode`` mirrors OPERATOR_MEET_JOIN_TARGET: phone / desktop / auto.
        """
        join = mode or meet_join_target()
        url = meet_video_url(meeting)
        title = meeting_title(meeting)
        timeout = accept_timeout if accept_timeout is not None else accept_timeout_s()
        tid = _clean_id(target_id)
        candidates = self._meeting_candidates(mode=join, target_id=tid)
        if not candidates:
            return DesktopDelivery(False, reason="no_client")

        last_reason = "no_client"
        for kind, cid in candidates:
            if kind == "local":
                station = self._locals.get(cid)
                if station is None or not station.eligible():
                    last_reason = "local_unavailable"
                    continue
                return DesktopDelivery(
                    True,
                    handler=cid,
                    command={
                        "id": f"local-{cid}",
                        "type": "local.meet_sip",
                        "payload": {
                            "title": title,
                            "e164": str(getattr(meeting, "e164", "") or ""),
                            "conference_id": str(
                                getattr(meeting, "conference_id", "") or ""
                            ),
                        },
                    },
                )
            if not url:
                last_reason = "no_meet_url"
                continue
            try:
                cmd = self.registry.enqueue_one(
                    cid,
                    "desktop.open_url",
                    {"url": url, "title": title},
                )
            except ValueError as e:
                last_reason = str(e)
                continue
            status = self.registry.wait_ack(cmd["id"], timeout)
            if status in ACCEPT_ACK_STATUSES:
                return DesktopDelivery(True, handler=cid, command=cmd)
            last_reason = status or "rejected"
        if not url and all(k == "desktop" for k, _ in candidates):
            return DesktopDelivery(False, reason="no_meet_url")
        return DesktopDelivery(False, reason=last_reason)

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

    def _meeting_candidates(
        self, *, mode: str, target_id: str = ""
    ) -> list[tuple[str, str]]:
        """Return ordered (kind, client_id) hops for open.meeting failover."""
        with self._prio_lock:
            order = list(self._priorities.get(INTENT_OPEN_MEETING) or [])
        online_desktop = self.registry._online_capable(capability="open_url")
        tid = _clean_id(target_id)
        if tid:
            order = [tid]

        # Append online desktops not listed (stable) so a new Mac still works.
        for cid in online_desktop:
            if cid not in order:
                order.append(cid)

        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for cid in order:
            if cid in seen:
                continue
            seen.add(cid)
            if cid in self._locals:
                if mode == "desktop":
                    continue
                station = self._locals[cid]
                if "open.meeting" not in station.capabilities and "open_url" not in station.capabilities:
                    continue
                if not station.eligible():
                    continue
                out.append(("local", cid))
                continue
            if mode == "phone":
                continue
            if cid in online_desktop:
                out.append(("desktop", cid))
        return out

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
        handler = ""
        if mode == _ROUTE_UNICAST:
            # Best-effort: preferred or first online.
            targets = self.registry._select_targets(
                capability=_capability_for(command_type),
                target_id=effective_target,
                mode=mode,
            )
            handler = targets[0] if targets else ""
        return DesktopDelivery(True, command=cmd, handler=handler)
