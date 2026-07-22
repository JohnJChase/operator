"""OpenAI HTTP helpers for the turn-based info desk (STT + tool chat)."""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def api_key_from_env() -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return key or None


def chat_model_from_env() -> str:
    return os.environ.get("OPERATOR_CHAT_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def _headers(api_key: str, *, json_body: bool = True) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _post_json(url: str, api_key: str, body: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=_headers(api_key), method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"OpenAI HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"OpenAI network error: {e}") from e


def transcribe_wav(path: Path, api_key: str, *, model: str = "whisper-1") -> str:
    """POST multipart audio/transcriptions; returns plain text."""
    boundary = f"----operator{uuid.uuid4().hex}"
    audio = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "audio/wav"
    chunks: list[bytes] = []

    def part(name: str, value: bytes, filename: str | None = None) -> None:
        chunks.append(f"--{boundary}\r\n".encode())
        if filename:
            chunks.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        else:
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value)
        chunks.append(b"\r\n")

    part("model", model.encode())
    part("file", audio, filename=path.name)
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    req = Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=90.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"STT HTTP {e.code}: {detail}") from e
    text = (payload.get("text") or "").strip()
    return text


def chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """One chat.completions round; returns the assistant message object."""
    openai_tools: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            openai_tools.append(t)
        else:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
    body = {
        "model": model or chat_model_from_env(),
        "messages": messages,
        "tools": openai_tools,
        "tool_choice": "auto",
    }
    data = _post_json("https://api.openai.com/v1/chat/completions", api_key, body)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI returned no choices")
    return choices[0]["message"]
