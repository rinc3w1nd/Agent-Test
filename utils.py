import os, time
from pathlib import Path

def now_ts_run() -> str:
    """Run-level timestamp: YYMMDD-HHMMSS (used in text/html headers)."""
    return time.strftime("%y%m%d-%H%M%S", time.localtime())

def now_ts_minute() -> str:
    """Minute-level timestamp: YYMMDD_HHMM (used in audit filename)."""
    return time.strftime("%y%m%d_%H%M", time.localtime())

def sanitize_id(s: str) -> str:
    """Restrict filenames to [A-Za-z0-9._-]."""
    s = (s or "").strip()
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    return "".join(ch if ch in keep else "_" for ch in s) or "item"

def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

def append_atomic(path: Path, chunk: str) -> None:
    """Append by read+rewrite atomically to avoid truncation on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write_text(path, chunk)
    else:
        existing = path.read_text(encoding="utf-8")
        _atomic_write_text(path, existing + chunk)