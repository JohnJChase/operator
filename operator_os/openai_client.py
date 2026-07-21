"""Minimal OpenAI HTTP client (stdlib only). Responses API + transcriptions.

Verified against official docs (2026): prefer Responses for tool loops;
use /v1/audio/transcriptions for turn-based STT. No openai SDK.
"""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class OpenAIError(RuntimeError):
    pass


def api_key_from_env() -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return key or None


def create_response(
    *,
    api_key: str,
    model: str,
    input: list[dict[str, Any]] | str,
    tools: list[dict[str, Any]] | None = None,
    instructions: str | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "input": input}
    if tools is not None:
        body["tools"] = tools
    if instructions is not None:
        body["instructions"] = instructions
    return _post_json(
        "https://api.openai.com/v1/responses",
        body,
        api_key=api_key,
        timeout_s=timeout_s,
    )


def transcribe_wav(
    path: Path,
    *,
    api_key: str,
    model: str = "gpt-4o-mini-transcribe",
    timeout_s: float = 60.0,
) -> str:
    data, content_type = _multipart_form(
        fields={"model": model},
        files={"file": path},
    )
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise OpenAIError(f"transcriptions HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise OpenAIError(f"transcriptions network: {e}") from e
    text = str(raw.get("text") or "").strip()
    if not text:
        raise OpenAIError("empty transcription")
    return text


def output_text(response: dict[str, Any]) -> str:
    """Collect assistant text from a Responses payload."""
    if text := str(response.get("output_text") or "").strip():
        return text
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                t = str(block.get("text") or "").strip()
                if t:
                    parts.append(t)
    return "\n".join(parts).strip()


def function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in response.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            out.append(item)
    return out


def _post_json(
    url: str,
    body: dict[str, Any],
    *,
    api_key: str,
    timeout_s: float,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise OpenAIError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise OpenAIError(f"network: {e}") from e


def _multipart_form(
    *,
    fields: dict[str, str],
    files: dict[str, Path],
) -> tuple[bytes, str]:
    boundary = f"----operator{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, path in files.items():
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
