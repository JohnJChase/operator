"""Minimal OpenAI helpers. Prefer Realtime WebSocket for the voice operator."""

from __future__ import annotations

import os


def api_key_from_env() -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return key or None
