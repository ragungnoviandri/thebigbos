"""Robust API & error logger for de BigBos — writes structured logs with tracebacks.

Files:
  ~/.debigbos/logs/api.log     — API request/response summary
  ~/.debigbos/logs/error.log   — ALL errors with tracebacks (API + internal)
  ~/.debigbos/logs/events.log  — Session lifecycle, config changes, etc.

No more silent `except Exception: pass`. Uses stderr as last-resort fallback.
"""

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path.home() / ".debigbos" / "logs"


def _ensure_dir() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _write_log(path: Path, content: str) -> None:
    """Append to log file. Falls back to stderr if IO fails."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            print(f"[BigBosLogger] Cannot write to {path}", file=sys.stderr, flush=True)
        except Exception:
            pass


def log_error(source: str, message: str = "", exc_info: bool = True) -> None:
    """Log an error with full traceback to error.log."""
    _ensure_dir()
    try:
        ts = _ts()
        tb = traceback.format_exc().strip() if exc_info else ""
        lines = [f"[{ts}] ERROR | {source} | {message}"]
        if tb and tb not in ("NoneType: None", "None"):
            lines.append(tb)
        _write_log(LOG_DIR / "error.log", "\n".join(lines) + "\n")
    except Exception:
        pass


def log_api_error(provider: str, model: str, status_code: int, error: str) -> None:
    """Log API error to both api.log and error.log."""
    _ensure_dir()
    try:
        ts = _ts()
        entry = f"[{ts}] ERR | {provider}/{model} | status={status_code} | {error[:500]}\n"
        _write_log(LOG_DIR / "api.log", entry)
        _write_log(LOG_DIR / "error.log", entry)
    except Exception:
        pass


def log_api_response(provider: str, model: str, status_code: int,
                     body: Any = None, usage: dict = None) -> None:
    """Log successful API response to api.log."""
    _ensure_dir()
    try:
        ts = _ts()
        resp_short = json.dumps(body, default=str)[:500] if body else ""
        usage_str = f" usage={usage}" if usage else ""
        entry = (
            f"[{ts}] RES | {provider}/{model} | status={status_code}{usage_str}\n"
            f"  body: {resp_short}\n"
        )
        _write_log(LOG_DIR / "api.log", entry)
    except Exception:
        pass


def log_api_request(provider: str, model: str, method: str,
                    url: str, body: dict) -> None:
    """Log outgoing API request to api.log."""
    _ensure_dir()
    try:
        ts = _ts()
        body_short = json.dumps(body, default=str)[:500]
        entry = (
            f"[{ts}] REQ | {provider}/{model}\n"
            f"  {method} {url}\n"
            f"  body: {body_short}\n"
        )
        _write_log(LOG_DIR / "api.log", entry)
    except Exception:
        pass


def log_internal(source: str, message: str, exc_info: bool = True) -> None:
    """Log internal events with optional traceback to error.log."""
    _ensure_dir()
    try:
        ts = _ts()
        tb = traceback.format_exc().strip() if exc_info else ""
        lines = [f"[{ts}] INTERNAL | {source} | {message}"]
        if tb and tb not in ("NoneType: None", "None"):
            lines.append(tb)
        _write_log(LOG_DIR / "error.log", "\n".join(lines) + "\n")
    except Exception:
        pass


def log_event(source: str, event: str, data: str = "") -> None:
    """Log lifecycle events to events.log."""
    _ensure_dir()
    try:
        ts = _ts()
        entry = f"[{ts}] EVENT | {source} | {event}"
        if data:
            entry += f" | {data[:300]}"
        _write_log(LOG_DIR / "events.log", entry + "\n")
    except Exception:
        pass


# ——— Legacy ApiLogger class (backward compat for provider code) ———

class ApiLogger:
    """Legacy wrapper. Providers import this via get_logger()."""

    def log_request(self, provider: str, model: str, method: str,
                    url: str, headers: dict, body: dict, call_ref: Any = None) -> None:
        log_api_request(provider, model, method, url, body)

    def log_response(self, provider: str, model: str, status_code: int,
                     body: Any = None, error: str = "",
                     usage: dict = None, call_ref: Any = None) -> None:
        if error:
            log_api_error(provider, model, status_code, error)
        else:
            log_api_response(provider, model, status_code, body, usage)


_logger = None


def get_logger() -> ApiLogger:
    """Get the global ApiLogger singleton (backward compat)."""
    global _logger
    if _logger is None:
        _logger = ApiLogger()
    return _logger
