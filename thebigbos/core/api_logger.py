"""API Logger — logs all LLM API requests & responses to thebigbos/logs/API.log.

Thread-safe, appends with clear visual separators. Integrates across all providers.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _get_log_path() -> Path:
    """Resolve the log file path relative to thebigbos package root."""
    # __file__ is .../thebigbos/core/api_logger.py → parent.parent = thebigbos/
    return Path(__file__).resolve().parent.parent / "logs" / "API.log"


# ── Public API ──────────────────────────────────────────────────


class APILogger:
    """Simple file-based API request/response logger."""

    def __init__(self, log_path: Path | None = None):
        self.path = log_path or _get_log_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Per-call timestamp tracking for elapsed time
        self._pending: dict[int, float] = {}  # id(call_context) → start_time

    # ── Request logging ──────────────────────────────────────

    def log_request(
        self,
        provider: str,
        model: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        call_ref: Any = None,
    ) -> None:
        """Log an outgoing API request."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        t = time.monotonic()
        # Track elapsed time using call_ref or fall back to body id
        key = id(call_ref) if call_ref is not None else id(body)
        self._pending[key] = t

        # Sanitize headers — strip auth tokens
        safe_headers = _sanitize_headers(headers)

        # Truncate messages for readability
        safe_body = _summarize_body(body)

        lines = [
            "╔" + "═" * 63,
            f"║ [{ts}]  REQUEST →  {provider}/{model}",
            "╠" + "═" * 63,
            f"║ {method} {url}",
            f"║ Headers: {json.dumps(safe_headers, indent=0, ensure_ascii=False)}",
            f"║ Body:",
        ]
        for line in json.dumps(safe_body, indent=2, ensure_ascii=False).split("\n"):
            lines.append(f"║   {line}")
        lines.append("╚" + "═" * 63)
        lines.append("")  # blank line before response

        _append(self.path, "\n".join(lines))

    # ── Response logging ──────────────────────────────────────

    def log_response(
        self,
        provider: str,
        model: str,
        status_code: int,
        body: Any = None,
        error: str | None = None,
        usage: dict[str, int] | None = None,
        call_ref: Any = None,
    ) -> None:
        """Log an incoming API response (or error)."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Calculate elapsed time
        elapsed = ""
        if call_ref is not None and id(call_ref) in self._pending:
            start = self._pending.pop(id(call_ref))
            elapsed = f"  ({time.monotonic() - start:.1f}s)"

        tag = "ERROR" if error else "RESPONSE"
        arrow = "←" if not error else "✗"

        lines = [
            f"╔{'═' * 63}",
            f"║ [{ts}]  {tag} {arrow}  {provider}/{model}{elapsed}",
        ]

        # Usage
        if usage and usage.get("total", 0) > 0:
            inp = usage.get("input", 0)
            out = usage.get("output", 0)
            tot = usage.get("total", inp + out)
            lines.append(f"║ Tokens: in={inp:,}  out={out:,}  total={tot:,}")

        lines.append(f"╠{'═' * 63}")

        if error:
            lines.append(f"║ Status: {status_code}")
            lines.append(f"║ Error: {error}")
        else:
            lines.append(f"║ Status: {status_code}")
            safe_body = _summarize_response(body)
            for line in json.dumps(safe_body, indent=2, ensure_ascii=False).split("\n"):
                lines.append(f"║   {line}")

        lines.append(f"╚{'═' * 63}")
        lines.append("")  # blank line before next entry

        _append(self.path, "\n".join(lines))


# ── Module-level convenience ────────────────────────────────────

_default_logger: APILogger | None = None


def get_logger() -> APILogger:
    """Get (or create) the global API logger instance."""
    global _default_logger
    if _default_logger is None:
        _default_logger = APILogger()
    return _default_logger


# ── Helpers ─────────────────────────────────────────────────────


def _append(path: Path, text: str) -> None:
    """Append text to file — safe, no exceptions propagate."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass  # Never crash the app for logging


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip sensitive values from headers."""
    sensitive = {"authorization", "x-api-key", "api-key", "cookie", "set-cookie"}
    out = {}
    for k, v in headers.items():
        if k.lower() in sensitive:
            out[k] = v[:15] + "..." if len(v) > 15 else v[:5] + "***"
        else:
            out[k] = v
    return out


def _summarize_body(body: dict[str, Any]) -> dict[str, Any]:
    """Truncate message contents in the request body for readability."""
    if not isinstance(body, dict):
        return body

    out = dict(body)
    # Truncate messages — only show role + first 200 chars
    if "messages" in out and isinstance(out["messages"], list):
        out["messages"] = [
            _truncate_message(m) for m in out["messages"]
        ]
    return out


def _truncate_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Truncate a single message's content."""
    if not isinstance(msg, dict):
        return msg
    m = dict(msg)
    content = m.get("content", "")
    if isinstance(content, str) and len(content) > 300:
        m["content"] = content[:300] + f"... [truncated, total {len(content)} chars]"
    elif isinstance(content, list):
        # Anthropic content blocks
        m["content"] = _truncate_content_blocks(content)
    return m


def _truncate_content_blocks(blocks: list) -> list:
    """Truncate Anthropic-style content blocks."""
    out = []
    for block in blocks:
        if isinstance(block, dict):
            b = dict(block)
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                t = b["text"]
                if len(t) > 300:
                    b["text"] = t[:300] + f"... [truncated, total {len(t)} chars]"
            elif b.get("type") == "tool_result":
                c = b.get("content", "")
                if isinstance(c, str) and len(c) > 200:
                    b["content"] = c[:200] + f"... [truncated]"
            out.append(b)
        else:
            out.append(block)
    return out


def _summarize_response(body: Any) -> Any:
    """Truncate response body for clean logging."""
    if not isinstance(body, dict):
        return body

    out = dict(body)
    # Truncate content
    if "content" in out and isinstance(out["content"], str):
        c = out["content"]
        if len(c) > 500:
            out["content"] = c[:500] + f"... [truncated, total {len(c)} chars]"

    # Truncate reasoning
    if "reasoning_content" in out and isinstance(out["reasoning_content"], str):
        rc = out["reasoning_content"]
        if len(rc) > 300:
            out["reasoning_content"] = rc[:300] + f"... [truncated, total {len(rc)} chars]"

    # Summarize tool calls
    if "tool_calls" in out and isinstance(out["tool_calls"], list):
        out["tool_calls"] = [
            {
                "id": tc.get("id", "")[:20],
                "name": tc.get("name", tc.get("function", {}).get("name", "")),
                "arguments": tc.get("arguments", tc.get("function", {}).get("arguments", {})),
            }
            for tc in out["tool_calls"]
        ]

    return out
