
#!/usr/bin/env python3
"""
Edge State Launcher (macOS-focused)
----------------------------------

Meets the explicit requirements:
1) Launch Edge with a user-selected state file (chosen from a list) so the user may interact with pages of their choosing.
2) If launched without a state file, prompt for a state name and create & save one when the browser is exited.

And the NFR set summarized:
- InPrivate mandatory (always --inprivate).
- "Private-Persisted" sessions via export/import of cookies + localStorage (Playwright/CDP style).
- State picker UI via prompt_toolkit with searchable list and "New state…" option.
- Timestamped, collision-proof state files: StateName_YYMMDDHHMM.state
- JSONL logs in ./logs/
- Encryption at rest for state files that contain auth artifacts, with passphrase derived via Argon2id and AES-GCM encryption (or macOS Keychain via keyring).
- Safety rail: forbids pointing at real Edge profile directories; uses temp user-data-dir per run.

Usage (after installing requirements and Playwright browsers):
    uv pip install -r requirements.txt  # or: pip install -r requirements.txt
    playwright install chromium
    python3 edge_launcher.py            # opens UI picker
    python3 edge_launcher.py --state Research  # direct

Notes:
- This script targets macOS (MBP). It will still run on other OSes but is tuned for Edge + macOS paths.
- Edge is launched via Playwright's Chromium with the Edge executable if discoverable; otherwise Chromium fallback.
"""

import asyncio
import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

# Third-party deps
from prompt_toolkit import prompt
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.validation import Validator, ValidationError

from playwright.async_api import async_playwright

# Encryption deps
try:
    import keyring  # macOS Keychain bridge (optional)
except Exception:
    keyring = None

try:
    from argon2.low_level import hash_secret_raw, Type as Argon2Type
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception as e:
    hash_secret_raw = None
    AESGCM = None

APP_NAME = "edge-launcher"
BASE_DIR = Path(".").resolve()
STATES_DIR = BASE_DIR / "states"
LOGS_DIR = BASE_DIR / "logs"
SCHEMA_VERSION = 1

STATES_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------- Utilities -------------------------

def ts_stamp() -> str:
    # Local time, 24h YYMMDDHHMM
    return datetime.now().strftime("%y%m%d%H%M")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def ulid_like() -> str:
    # Simple ULID-like (not monotonic): time-based + random
    import os, base64
    millis = int(time.time() * 1000).to_bytes(6, "big")
    rand = os.urandom(10)
    return base64.b32encode(millis + rand).decode("ascii").rstrip("=").lower()

def slugify(name: str) -> str:
    s = name.strip()
    s = re.sub(r"[^\w\s\-\.]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s).strip("-")
    if not s:
        s = "state"
    return s[:64]

def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
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
        # Within-minute collision handling
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

# ------------------------- Encryption helpers -------------------------

class Encryptor:
    """
    AES-GCM with Argon2id key derivation.
    If keyring is available and --keychain is set, a per-state secret is stored/retrieved.
    """
    def __init__(self, enabled: bool, use_keychain: bool, state_name: str):
        self.enabled = enabled
        self.use_keychain = use_keychain and keyring is not None
        self.state_name = state_name

    def _derive_key(self, passphrase: bytes, salt: bytes) -> bytes:
        if hash_secret_raw is None:
            raise RuntimeError("Argon2id unavailable. Install argon2-cffi.")
        return hash_secret_raw(
            secret=passphrase,
            salt=salt,
            time_cost=2,
            memory_cost=102400,  # ~100 MiB
            parallelism=8,
            hash_len=32,
            type=Argon2Type.ID,
        )

    def _get_or_create_secret(self) -> bytes:
        if self.use_keychain:
            stored = keyring.get_password(APP_NAME, self.state_name)
            if stored:
                return stored.encode("utf-8")
            # create one
            import secrets, string
            alphabet = string.ascii_letters + string.digits
            secret = "".join(secrets.choice(alphabet) for _ in range(32))
            keyring.set_password(APP_NAME, self.state_name, secret)
            return secret.encode("utf-8")
        # fallback to interactive prompt
        import getpass
        pw = getpass.getpass(f"Passphrase for state '{self.state_name}': ").encode("utf-8")
        if not pw:
            raise RuntimeError("Empty passphrase not allowed when encryption is enabled.")
        return pw

    def encrypt(self, data: bytes) -> bytes:
        if not self.enabled:
            return data
        if AESGCM is None:
            raise RuntimeError("cryptography AESGCM unavailable. Install 'cryptography'.")
        pw = self._get_or_create_secret()
        import os, struct
        salt = os.urandom(16)
        key = self._derive_key(pw, salt)
        aes = AESGCM(key)
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, data, None)
        # Format: b'AGCM' + salt(16) + nonce(12) + ct
        return b"AGCM" + salt + nonce + ct

    def decrypt(self, blob: bytes) -> bytes:
        if not self.enabled:
            return blob
        if blob.startswith(b"AGCM"):
            if AESGCM is None:
                raise RuntimeError("cryptography AESGCM unavailable. Install 'cryptography'.")
            salt = blob[4:20]
            nonce = blob[20:32]
            ct = blob[32:]
            pw = self._get_or_create_secret()
            key = self._derive_key(pw, salt)
            aes = AESGCM(key)
            return aes.decrypt(nonce, ct, None)
        # If header absent, treat as plaintext legacy and return as-is
        return blob

# ------------------------- TUI -------------------------

def pick_or_create_state_interactive() -> Tuple[Path, bool, str]:
    items = list_states()
    display = [f"➕ New state…" ] + [f"{name} — {created}" if created else name for (name, fn, created) in items]
    completer = FuzzyWordCompleter(display, WORD=True, sentence=True)

    choice = prompt("Select state (type to filter, Enter to choose): ", completer=completer)
    if choice.strip().startswith("➕"):
        raw = prompt("New state name: ").strip()
        slug = slugify(raw)
        path = state_filename_for(slug)
        return path, True, slug
    else:
        # map back to file
        try:
            idx = display.index(choice)
        except ValueError:
            # try fuzzy match to closest
            norm = choice.strip().lower()
            idx = next((i for i, s in enumerate(display) if s.lower() == norm), -1)
        if idx <= 0:
            # invalid -> create new
            slug = slugify(choice or "state")
            path = state_filename_for(slug)
            return path, True, slug
        name, fn, created = items[idx - 1]
        return STATES_DIR / fn, False, slugify(name)

# ------------------------- Playwright storage helpers -------------------------

async def restore_storage_to_context(context, state_json: Dict[str, Any], session_id: str) -> None:
    # Cookies
    cookies = state_json.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)
        log_json(session_id, "restore.cookies", count=len(cookies))

    # LocalStorage
    origins = state_json.get("origins") or []
    for item in origins:
        origin = item.get("origin")
        ls = item.get("localStorage") or []
        if not origin or not ls:
            continue
        page = await context.new_page()
        try:
            await page.goto(origin, wait_until="domcontentloaded")
            for kv in ls:
                try:
                    await page.evaluate("""([k, v]) => localStorage.setItem(k, v)""", [kv["name"], kv["value"]])
                except Exception:
                    pass
            log_json(session_id, "restore.localStorage", origin=origin, keys=len(ls))
        except Exception as e:
            log_json(session_id, "restore.warn", origin=origin, error=str(e))
        finally:
            await page.close()

async def dump_storage_from_context(context, visited_origins: Set[str], session_id: str) -> Dict[str, Any]:
    result = {"version": SCHEMA_VERSION, "cookies": [], "origins": []}
    # Normalize origins to scheme+host
    origins = sorted(set(visited_origins))
    # Cookies: gather per-origin for reliability
    if origins:
        page = await context.new_page()
        try:
            for o in origins:
                try:
                    await page.goto(o, wait_until="domcontentloaded")
                    ck = await context.cookies(o)
                    result["cookies"].extend(ck)
                except Exception:
                    pass
        finally:
            await page.close()
    # LocalStorage
    for o in origins:
        pg = await context.new_page()
        try:
            await pg.goto(o, wait_until="domcontentloaded")
            kvs = await pg.evaluate("""() => {
                const out=[]; for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i); out.push({name:k, value:localStorage.getItem(k)});} return out;
            }""")
            if kvs:
                result["origins"].append({"origin": o, "localStorage": kvs})
        except Exception:
            pass
        finally:
            await pg.close()
    log_json(session_id, "dump.summary", origins=len(origins), cookies=len(result["cookies"]), ls_origins=len(result["origins"]))
    return result

def origin_of_url(url: str) -> Optional[str]:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        if not u.scheme or not u.netloc:
            return None
        return f"{u.scheme}://{u.netloc}"
    except Exception:
        return None

# ------------------------- Edge discovery -------------------------

def discover_edge_executable() -> Optional[str]:
    candidates = [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Microsoft Edge Beta.app/Contents/MacOS/Microsoft Edge Beta",
        "/Applications/Microsoft Edge Dev.app/Contents/MacOS/Microsoft Edge Dev",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None

# ------------------------- Main -------------------------

async def run(args) -> int:
    session_id = ulid_like()
    log_json(session_id, "start", version=SCHEMA_VERSION)

    # State selection
    created_now = False
    state_name = None
    if args.state:
        # Treat as bare name unless path-like
        p = Path(args.state)
        if p.suffix == "":
            state_name = slugify(p.name)
            state_path = STATES_DIR / f"{state_name}.state" if (STATES_DIR / f"{state_name}.state").exists() else state_filename_for(state_name)
            created_now = not state_path.exists()
        else:
            state_path = p
            state_name = slugify(p.stem.split("_")[0] if "_" in p.stem else p.stem)
            created_now = not state_path.exists()
    else:
        state_path, created_now, state_name = pick_or_create_state_interactive()

    # Safety rail: forbid real profile directories (we never touch them)
    risky = str(state_path).lower().startswith(str(Path.home() / "library" / "application support" / "microsoft edge").lower())
    if risky:
        log_json(session_id, "error", code="E_RISKY_PATH", path=str(state_path))
        print("Refusing to use a real Edge profile path. Choose a state under ./states/.")
        return 40

    # Prepare encryptor
    encryptor = Encryptor(enabled=not args.no_encrypt, use_keychain=not args.no_keychain, state_name=state_name or "state")

    # Load state (if exists)
    init_state: Optional[Dict[str, Any]] = None
    if state_path.exists():
        try:
            blob = state_path.read_bytes()
            try:
                blob = encryptor.decrypt(blob)
            except Exception as e:
                log_json(session_id, "decrypt.error", error=str(e))
                print("Decryption failed. Wrong passphrase or missing Keychain item.")
                return 30
            init_state = json.loads(blob.decode("utf-8"))
            log_json(session_id, "state.loaded", path=str(state_path))
        except Exception as e:
            log_json(session_id, "state.load_error", error=str(e))
            init_state = None  # start clean

    # Launch Edge (InPrivate) with temp user-data-dir
    edge_exec = discover_edge_executable()
    launch_args = ["--inprivate"]
    with tempfile.TemporaryDirectory(prefix="edge-np-") as tmpdir:
        user_data_arg = f"--user-data-dir={tmpdir}"
        launch_args.append(user_data_arg)
        if args.edge_arg:
            launch_args.extend(args.edge_arg)

        async with async_playwright() as pw:
            # Use Edge executable if found; else chromium default
            browser = await pw.chromium.launch(
                headless=False,
                executable_path=edge_exec,
                args=launch_args
            )
            context = await browser.new_context()
            visited_origins: Set[str] = set()

            # Track navigations to learn origins to dump later
            def on_navigate(frame):
                if frame.url:
                    o = origin_of_url(frame.url)
                    if o:
                        visited_origins.add(o)

            context.on("framenavigated", on_navigate)

            # Restore storage
            if init_state:
                await restore_storage_to_context(context, init_state, session_id)

            # Open initial page (optional)
            page = await context.new_page()
            start_url = args.url or "https://www.microsoft.com/"
            await page.goto(start_url)

            # Wait for the browser to close last page
            try:
                await page.wait_for_event("close")
            except Exception:
                pass

            # Dump storage
            state_json = await dump_storage_from_context(context, visited_origins, session_id)

            # Decide whether to encrypt (mandatory if cookies or LS present and encryption enabled)
            data = json.dumps(state_json, ensure_ascii=False, indent=None).encode("utf-8")
            try:
                blob = encryptor.encrypt(data) if (not args.no_encrypt and (state_json.get("cookies") or state_json.get("origins"))) else data
            except Exception as e:
                log_json(session_id, "encrypt.error", error=str(e))
                print("Encryption failed; state will NOT be written.")
                blob = None

            # Write state
            if blob is not None:
                # Ensure filename format if we just created it
                if created_now and state_path.parent.resolve() == STATES_DIR.resolve():
                    # If user supplied a bare name without timestamp, upgrade it
                    if not re.search(r"_\d{10}(-\d+)?\.state$", state_path.name):
                        state_path = state_filename_for(state_name or "state")
                atomic_write_text(state_path, blob.decode("utf-8") if isinstance(blob, bytes) == False else "") if False else state_path.write_bytes(blob)
                os.chmod(state_path, 0o600)
                log_json(session_id, "state.saved", path=str(state_path), bytes=len(blob))

            await context.close()
            await browser.close()

    log_json(session_id, "exit", code=0)
    return 0

def build_arg_parser():
    ap = argparse.ArgumentParser(description="Launch Edge in InPrivate with a selected state; create/save state on exit if new.")
    ap.add_argument("--state", help="State name or path. If omitted, shows a picker.")
    ap.add_argument("--url", default="https://teams.microsoft.com", help="Initial URL to open.")
    ap.add_argument("--edge-arg", action="append", default=[], help="Additional flags to pass to Edge.")
    ap.add_argument("--no-encrypt", action="store_true", help="Disable encryption at rest (not recommended).")
    ap.add_argument("--no-keychain", action="store_true", help="Disable macOS Keychain usage for state secrets.")
    return ap

def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        rc = asyncio.run(run(args))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)

if __name__ == "__main__":
    main()
