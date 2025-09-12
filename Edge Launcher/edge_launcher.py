
#!/usr/bin/env python3
"""
Edge State Launcher (macOS-focused) — Fixed (FuzzyCompleter)
"""

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter, FuzzyCompleter
from playwright.async_api import async_playwright

try:
    import keyring
except Exception:
    keyring = None

try:
    from argon2.low_level import hash_secret_raw, Type as Argon2Type
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:
    hash_secret_raw = None
    AESGCM = None

APP_NAME = "edge-launcher"
BASE_DIR = Path(".").resolve()
STATES_DIR = BASE_DIR / "states"
LOGS_DIR = BASE_DIR / "logs"
SCHEMA_VERSION = 1

STATES_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

def ts_stamp() -> str:
    return datetime.now().strftime("%y%m%d%H%M")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def ulid_like() -> str:
    import base64, os as _os
    millis = int(time.time() * 1000).to_bytes(6, "big")
    rand = _os.urandom(10)
    return base64.b32encode(millis + rand).decode("ascii").rstrip("=").lower()

def slugify(name: str) -> str:
    s = name.strip()
    s = re.sub(r"[^\w\s\-\.]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s[:64] if s else "state"

def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)

def log_json(session_id: str, event: str, **fields: Any) -> None:
    rec = {"ts": now_iso(), "session": session_id, "event": event, **fields}
    line = json.dumps(rec, ensure_ascii=False)
    print(line)
    log_file = LOGS_DIR / f"{APP_NAME}_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def state_filename_for(name: str) -> Path:
    base = f"{name}_{ts_stamp()}.state"
    p = STATES_DIR / base
    suffix = 2
    while p.exists():
        p = STATES_DIR / f"{name}_{ts_stamp()}-{suffix}.state"
        suffix += 1
    return p

def list_states() -> List[Tuple[str, str, str]]:
    items = []
    for p in sorted(STATES_DIR.glob("*.state")):
        stem = p.stem
        created = ""
        name = stem
        if "_" in stem:
            name, ts = stem.rsplit("_", 1)
            try:
                created = datetime.strptime(ts.split("-")[0], "%y%m%d%H%M").strftime("%Y-%m-%d %H:%M")
            except Exception:
                created = ""
        items.append((name, p.name, created))
    return items

class Encryptor:
    def __init__(self, enabled: bool, use_keychain: bool, state_name: str):
        self.enabled = enabled
        self.use_keychain = use_keychain and keyring is not None
        self.state_name = state_name
    def _derive_key(self, passphrase: bytes, salt: bytes) -> bytes:
        if hash_secret_raw is None:
            raise RuntimeError("Argon2id unavailable.")
        return hash_secret_raw(secret=passphrase, salt=salt, time_cost=2, memory_cost=102400, parallelism=8, hash_len=32, type=Argon2Type.ID)
    def _get_or_create_secret(self) -> bytes:
        if self.use_keychain:
            stored = keyring.get_password(APP_NAME, self.state_name)
            if stored:
                return stored.encode("utf-8")
            import secrets, string
            secret = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
            keyring.set_password(APP_NAME, self.state_name, secret)
            return secret.encode("utf-8")
        import getpass
        pw = getpass.getpass(f"Passphrase for state '{self.state_name}': ").encode("utf-8")
        if not pw:
            raise RuntimeError("Empty passphrase not allowed.")
        return pw
    def encrypt(self, data: bytes) -> bytes:
        if not self.enabled:
            return data
        if AESGCM is None:
            raise RuntimeError("AESGCM unavailable.")
        import os as _os
        salt = _os.urandom(16)
        key = self._derive_key(self._get_or_create_secret(), salt)
        aes = AESGCM(key)
        nonce = _os.urandom(12)
        ct = aes.encrypt(nonce, data, None)
        return b"AGCM" + salt + nonce + ct
    def decrypt(self, blob: bytes) -> bytes:
        if not self.enabled:
            return blob
        if blob.startswith(b"AGCM"):
            if AESGCM is None:
                raise RuntimeError("AESGCM unavailable.")
            salt = blob[4:20]
            nonce = blob[20:32]
            ct = blob[32:]
            key = self._derive_key(self._get_or_create_secret(), salt)
            aes = AESGCM(key)
            return aes.decrypt(nonce, ct, None)
        return blob

def pick_or_create_state_interactive() -> Tuple[Path, bool, str]:
    items = list_states()
    display = ["➕ New state…"] + [f"{name} — {created}" if created else name for (name, fn, created) in items]
    completer = FuzzyCompleter(WordCompleter(display, ignore_case=True))
    choice = prompt("Select state (type to filter, Enter to choose): ", completer=completer)
    if choice.strip().startswith("➕"):
        raw = prompt("New state name: ").strip()
        slug = slugify(raw)
        path = state_filename_for(slug)
        return path, True, slug
    else:
        try:
            idx = display.index(choice)
        except ValueError:
            slug = slugify(choice or "state")
            path = state_filename_for(slug)
            return path, True, slug
        if idx == 0:
            slug = slugify("state")
            path = state_filename_for(slug)
            return path, True, slug
        name, fn, _ = items[idx - 1]
        return STATES_DIR / fn, False, slugify(name)

# (rest of script unchanged from previous fixed version; omitted here for brevity)
# ... would include restore_storage_to_context, dump_storage_from_context, discover_edge_executable, run(), etc.
