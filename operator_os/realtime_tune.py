"""Realtime mic/VAD tuning bench — arrow keys, live RMS gauge."""

from __future__ import annotations

import curses
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile
from operator_os.events import EventLog
from operator_os.local_tools import LocalTools, build_status_snapshot
from operator_os.openai_client import api_key_from_env
from operator_os.realtime_operator import UNAVAILABLE, RealtimeSession
from operator_os.refresh import load_dotenv

# Gauge span (dBFS). Carbon mic idle often sits around -50..-35.
_DB_LO = -60.0
_DB_HI = 0.0


@dataclass(frozen=True)
class Knob:
    key: str
    label: str
    step: float
    fmt: str
    coerce: Callable[[float], Any]


KNOBS: tuple[Knob, ...] = (
    Knob("capture_gain", "mic gain ×", 0.25, "{:.2f}", lambda x: round(x, 2)),
    Knob("playback_gain", "ear gain ×", 0.05, "{:.2f}", lambda x: round(x, 2)),
    Knob("mic_gate_dbfs", "gate dBFS", 1.0, "{:+.0f}", lambda x: round(x)),
    Knob("mic_hangover_ms", "hangover ms", 50.0, "{:.0f}", lambda x: int(round(x))),
    Knob("echo_guard_ms", "echo guard ms", 50.0, "{:.0f}", lambda x: int(round(x))),
    Knob("vad_threshold", "VAD threshold", 0.05, "{:.2f}", lambda x: round(x, 2)),
    Knob("vad_silence_ms", "silence ms", 50.0, "{:.0f}", lambda x: int(round(x))),
    Knob("vad_prefix_ms", "prefix ms", 50.0, "{:.0f}", lambda x: int(round(x))),
)

NOISE_CYCLE = ("near_field", "far_field", "off")

# Color pair ids
_C_CLOSED = 1
_C_OPEN = 2
_C_TALK = 3
_C_RMS = 4
_C_MARK = 5


def run_realtime_tune(
    profile: HardwareProfile, *, profile_path: str = "config/hardware_profile.yaml"
) -> int:
    load_dotenv()
    key = api_key_from_env()
    if not key:
        print(UNAVAILABLE, file=sys.stderr)
        return 1

    from operator_os.phone import GpioPhone, attach_hook_cutoff, wait_off_hook

    # Hook first (before slow Piper load) so "Lift handset" is immediate.
    phone = GpioPhone(profile)
    print("Hang up anytime to stop.", flush=True)
    wait_off_hook(phone)

    audio = AudioRouter(profile.audio)
    events = EventLog()
    tools = LocalTools(
        audio=audio,
        profile=profile,
        status_snapshot=build_status_snapshot(profile.name),
        voice_mode=True,
    )
    session = RealtimeSession(
        audio=audio,
        events=events,
        tools=tools,
        api_key=key,
        realtime_cfg=dict(profile.raw.get("realtime") or {}),
        auto_greet=False,
    )
    attach_hook_cutoff(phone, audio, on_hangup=session.cancel_now)
    audio.set_hook(True)
    session.start()

    if not session.wait_ready(10.0) or audio.is_on_hook:
        if not audio.is_on_hook:
            print("session failed to become ready", file=sys.stderr)
        session.cancel_now()
        phone.close()
        audio.close()
        return 0 if audio.is_on_hook else 1

    print("Ear to receiver — greeting now.", flush=True)
    session.greet()
    time.sleep(0.5)

    try:
        if not audio.is_on_hook:
            curses.wrapper(lambda stdscr: _ui_loop(stdscr, session, profile_path))
    except KeyboardInterrupt:
        pass
    finally:
        session.cancel_now()
        for _ in range(30):
            if not session.is_alive():
                break
            time.sleep(0.1)
        phone.close()
        audio.close()
    return 0


def _ui_loop(stdscr: curses.window, session: RealtimeSession, profile_path: str) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    stdscr.timeout(100)
    _init_colors()

    selected = 0
    status = "Space=interrupt  ↑↓ tweak  (raise mic gain, lower ear gain if bleed)"
    dirty = False

    while session.is_alive() and not session.audio.is_on_hook:
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            break

        if ch == ord("q") or ch == 27:
            break
        if ch == ord(" "):
            session.interrupt()
            status = "interrupted (Space)"
        elif ch in (curses.KEY_UP, ord("k")):
            _nudge(session, KNOBS[selected], +1)
            dirty = True
            status = f"adjusted {KNOBS[selected].label}"
        elif ch in (curses.KEY_DOWN, ord("j")):
            _nudge(session, KNOBS[selected], -1)
            dirty = True
            status = f"adjusted {KNOBS[selected].label}"
        elif ch in (curses.KEY_LEFT, ord("h")):
            selected = (selected - 1) % len(KNOBS)
        elif ch in (curses.KEY_RIGHT, ord("l")):
            selected = (selected + 1) % len(KNOBS)
        elif ch == 9:
            selected = (selected + 1) % len(KNOBS)
        elif ch == ord("n"):
            cur = str(session.cfg.get("noise_reduction") or "near_field")
            nxt = (
                NOISE_CYCLE[(NOISE_CYCLE.index(cur) + 1) % len(NOISE_CYCLE)]
                if cur in NOISE_CYCLE
                else NOISE_CYCLE[0]
            )
            session.update_cfg(noise_reduction=nxt)
            dirty = True
            status = f"noise → {nxt}"
        elif ch == ord("g"):
            session.greet()
            status = "greeting…"
        elif ch == ord("c"):
            session.clear_input()
            status = "cleared input buffer"
        elif ch == ord("w"):
            path = session.save_cfg(profile_path)
            dirty = False
            status = f"wrote {path}"
        elif ch == ord("?"):
            status = "Space interrupt  ←→ select  ↑↓ change  g greet  w write  q quit"

        _draw(stdscr, session, selected, status, dirty)
        time.sleep(0.02)


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_C_CLOSED, curses.COLOR_BLACK, curses.COLOR_RED)
    curses.init_pair(_C_OPEN, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(_C_TALK, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(_C_RMS, curses.COLOR_CYAN, -1)
    curses.init_pair(_C_MARK, curses.COLOR_WHITE, -1)


def _nudge(session: RealtimeSession, knob: Knob, direction: int) -> None:
    cur = float(session.cfg.get(knob.key, 0))
    new = knob.coerce(cur + direction * knob.step)
    if knob.key == "vad_threshold":
        new = max(0.0, min(1.0, new))
    if knob.key in (
        "mic_hangover_ms",
        "echo_guard_ms",
        "vad_silence_ms",
        "vad_prefix_ms",
    ):
        new = max(0, new)
    if knob.key == "capture_gain":
        new = max(0.25, min(12.0, new))
    if knob.key == "playback_gain":
        new = max(0.05, min(1.0, new))
    session.update_cfg(**{knob.key: new})


def _db_to_x(db: float, width: int) -> int:
    if width <= 1:
        return 0
    t = (db - _DB_LO) / (_DB_HI - _DB_LO)
    return max(0, min(width - 1, int(round(t * (width - 1)))))


def _draw(
    stdscr: curses.window,
    session: RealtimeSession,
    selected: int,
    status: str,
    dirty: bool,
) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    colors = curses.has_colors()

    def put(y: int, x: int, text: str, attr: int = 0) -> None:
        if y < 0 or y >= h or x >= w:
            return
        stdscr.addnstr(y, x, text[: max(0, w - x - 1)], max(0, w - x - 1), attr)

    def row(y: int, text: str, attr: int = 0) -> None:
        put(y, 0, text.ljust(max(0, w - 1)), attr)

    rms = session.last_rms_dbfs
    raw = session.last_raw_rms_dbfs
    gate = float(session.cfg.get("mic_gate_dbfs", -40))
    hang = float(session.cfg.get("mic_hangover_ms", 400))
    echo = float(session.cfg.get("echo_guard_ms", 500))
    cg = float(session.cfg.get("capture_gain", 8.0))
    pg = float(session.cfg.get("playback_gain", 0.55))
    talking = session._piper_playing
    echoing = session.echo_guarding
    door = session.uplink_open
    above_gate = rms >= gate

    if talking:
        door_label = "  PIPER TALKING — Space to interrupt  "
        door_attr = curses.color_pair(_C_TALK) if colors else curses.A_REVERSE
    elif echoing:
        door_label = "  ECHO GUARD  →  waiting out earpiece bleed  "
        door_attr = curses.color_pair(_C_TALK) if colors else curses.A_REVERSE
    elif door:
        door_label = "  DOOR OPEN  →  mic audio going to OpenAI  "
        door_attr = curses.color_pair(_C_OPEN) if colors else curses.A_REVERSE
    else:
        door_label = "  DOOR CLOSED  →  silence / below gate  "
        door_attr = curses.color_pair(_C_CLOSED) if colors else curses.A_DIM

    row(0, "WE302 Realtime Tune")
    row(1, door_label.center(max(10, w - 1)), door_attr)
    row(
        2,
        f"raw={raw:+5.1f}dB  after mic×{cg:.2f} → {rms:+5.1f}dB   "
        f"gate={gate:+.0f}  ear×{pg:.2f}  hang={hang:.0f}ms  echo={echo:.0f}ms",
    )

    # --- dB gauge ---
    gauge_x0 = 6
    gauge_w = max(10, w - gauge_x0 - 2)
    y_scale = 4
    y_zones = 5
    y_marks = 6
    y_level = 7
    y_leg = 8

    # Scale ticks
    scale = [" "] * gauge_w
    for tick_db, label in ((-60, "-60"), (-40, "-40"), (-20, "-20"), (0, "0")):
        tx = _db_to_x(tick_db, gauge_w)
        for i, ch in enumerate(label):
            if 0 <= tx + i < gauge_w:
                scale[tx + i] = ch
    put(y_scale, 0, "dBFS")
    put(y_scale, gauge_x0, "".join(scale))

    # Zone bar: closed (left of gate) / open (right of gate)
    gx = _db_to_x(gate, gauge_w)
    for i in range(gauge_w):
        if i < gx:
            ch, attr = "░", curses.color_pair(_C_CLOSED) if colors else curses.A_DIM
        else:
            ch, attr = "█", curses.color_pair(_C_OPEN) if colors else curses.A_BOLD
        put(y_zones, gauge_x0 + i, ch, attr)
    put(y_zones, 0, "zone")

    # Marker row: G gate, * rms
    marks = [" "] * gauge_w
    mark_attrs = [0] * gauge_w

    def place(db: float, glyph: str, attr: int) -> None:
        x = _db_to_x(db, gauge_w)
        marks[x] = glyph
        mark_attrs[x] = attr

    place(gate, "G", curses.color_pair(_C_MARK) | curses.A_BOLD if colors else curses.A_BOLD)
    place(rms, "*", curses.color_pair(_C_RMS) | curses.A_BOLD if colors else curses.A_BOLD)
    put(y_marks, 0, "mark")
    for i, ch in enumerate(marks):
        put(y_marks, gauge_x0 + i, ch, mark_attrs[i])

    # Level fill to current RMS
    rx = _db_to_x(rms, gauge_w)
    put(y_level, 0, "lvl ")
    for i in range(gauge_w):
        if i <= rx:
            if i < gx:
                attr = curses.color_pair(_C_CLOSED) if colors else curses.A_DIM
            else:
                attr = curses.color_pair(_C_OPEN) if colors else curses.A_BOLD
            put(y_level, gauge_x0 + i, "═", attr)
        else:
            put(y_level, gauge_x0 + i, "─", curses.A_DIM)

    put(
        y_leg,
        0,
        "G=gate  *=mic after gain   raise mic× if speech never opens door",
    )

    # Instantaneous vs hangover note
    if above_gate and not door and not talking:
        note = "above gate — hangover not open yet (or just closed)"
    elif door and not above_gate:
        note = "DOOR OPEN on hangover (level dropped below gate)"
    else:
        note = ""
    if note:
        put(y_leg + 1, 0, f"→ {note}")

    # --- Knobs ---
    y0 = y_leg + 3
    row(y0, "  knob                  value")
    row(y0 + 1, "  --------------------- --------")
    for i, knob in enumerate(KNOBS):
        val = session.cfg.get(knob.key, "")
        try:
            shown = knob.fmt.format(float(val))
        except (TypeError, ValueError):
            shown = str(val)
        mark = ">" if i == selected else " "
        attr = curses.A_REVERSE if i == selected else 0
        row(y0 + 2 + i, f" {mark} {knob.label:<21} {shown}", attr)

    y = y0 + 2 + len(KNOBS)
    row(y + 1, f"  noise (n cycles)      {session.cfg.get('noise_reduction')}")
    row(y + 3, "Space=INTERRUPT  ←→ select  ↑↓ change  n noise  g greet  c clear  w write  q quit")
    flag = " *unsaved*" if dirty else ""
    row(y + 4, f"status: {status}{flag}")
    stdscr.refresh()
