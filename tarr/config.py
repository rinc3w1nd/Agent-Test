from pathlib import Path
from typing import Any, Dict, Optional
import os
try:
    import yaml
except Exception:
    yaml = None

DEFAULTS: Dict[str, Any] = {
    "config_version": 3,
    "browser_channel": "msedge",
    "headless": False,

    # Non-persistent + storage state (per NFR R1)
    "storage_state_path": "auth/auth_state.json",

    # Target
    "teams_channel_url": "",
    "bot_name": "",

    # Mention timing (NFR P2 = 5/5/5 windows by default)
    "mention_delay_before_at_ms": 15000,
    "mention_type_char_delay_ms": 35,
    "mention_popup_wait_ms": 5000,
    "mention_retype_wait_ms": 500,
    "mention_retype_attempts": 2,
    "mention_retype_backoff": True,
    "mention_attempt_windows_ms": [5000, 5000, 5000],

    # Readiness waits (NFR P1 doubled)
    "dom_ready_timeout_ms": 120000,
    "message_list_timeout_ms": 240000,
    "reply_timeout_ms": 120000,

    # Artifacts & audit (NFR O2, D1–D4)
    "artifacts_root": "artifacts",
    "text_dir": "artifacts/text",
    "html_dir": "artifacts/html",
    "screens_dir": "artifacts/screens",
    "audit_dir": "audit",
}

def load(path: Optional[str]) -> Dict[str, Any]:
    cfg: Dict[str, Any] = dict(DEFAULTS)
    if path and Path(path).exists() and yaml is not None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cfg.update(data)

    # Normalize important paths
    for k in ("text_dir", "html_dir", "screens_dir", "audit_dir", "storage_state_path"):
        cfg[k] = os.path.expanduser(os.path.expandvars(str(cfg.get(k, DEFAULTS[k]))))
    return cfg