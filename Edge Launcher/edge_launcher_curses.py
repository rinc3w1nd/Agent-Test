#!/usr/bin/env python3
"""
Edge State Launcher -- CLI-only with curses UI (non-persistent + InPrivate)
- Lists states only from ./state/
- ↑/↓ select • ENTER launch • n new • d delete • q quit
- Always launches Edge InPrivate (non-persistent Playwright browser)
- Simulated persistence by importing/exporting cookies + localStorage
- Timestamped state filenames: Name_YYMMDDHHMM.state
- Logs JSONL to ./logs/
- Encrypts state files (AES-GCM + Argon2id) via macOS Keychain if available, else passphrase
"""

import argparse
import asyncio
import curses
import curses.textpad
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.async_api import async_playwright

# Optional encryption/keychain deps
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
STATE_DIR = BASE_DIR / "state"   # singular, as requested
LOGS_DIR = BASE_DIR / "logs"
SCHEMA_VERSION = 1

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------- utils ----------
def ts_stamp() -> str:
    return datetime.now().strftime("%y%m%d%H%M")  # local, 24h

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
    p = STATE_DIR / base
    suffix = 2
    while p.exists():
        p = STATE_DIR / f"{name}_{ts_stamp()}-{suffix}.state"
        suffix += 1
    return p

def list_states() -> List[Tuple[str, Path, str]]:
    items = []
    for p in sorted(STATE_DIR.glob("*.state")):
        stem = p.stem
        created = ""
        name = stem
        if "_" in stem:
            name, ts = stem.rsplit("_", 1)
            try:
                created = datetime.strptime(ts.split("-")[0], "%y%m%d%H%M").strftime("%Y-%m-%d %H:%M")
            except Exception:
                created = ""
        items.append((name, p, created))
    return items

# ---------- encryption ----------
class Encryptor:
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
            memory_cost=102400,
            parallelism=8,
            hash_len=32,
            type=Argon2Type.ID,
        )

    def _get_or_create_secret(self) -> bytes:
        if self.use_keychain:
            stored = keyring.get_password(APP_NAME, self.state_name)
            if stored:
                return stored.encode("utf-8")
            import secrets, string
            alphabet = string.ascii_letters + string.digits
            secret = "".join(secrets.choice(alphabet) for _ in range(32))
            keyring.set_password(APP_NAME, self.state_name, secret)
            return secret.encode("utf-8")
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
                raise RuntimeError("cryptography AESGCM unavailable. Install 'cryptography'.")
            salt = blob[4:20]
            nonce = blob[20:32]
            ct = blob[32:]
            key = self._derive_key(self._get_or_create_secret(), salt)
            aes = AESGCM(key)
            return aes.decrypt(nonce, ct, None)
        return blob  # legacy plaintext support

# ---------- curses UI ----------
class CursesUI:
    HELP = "↑/↓ select  •  ENTER launch  •  n new  •  d delete  •  q quit"
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        self.selected = 0
        self.refresh_items()
    def refresh_items(self):
        self.items = list_states()
        if self.selected >= len(self.items):
            self.selected = max(0, len(self.items) - 1)
    def draw(self, msg: str = ""):
        self.stdscr.clear()
        h, w = self.stdscr.getmaxyx()
        title = f"Edge State Launcher -- ./state/  ({len(self.items)} states)"
        self.stdscr.addstr(0, 0, title[:w-1], curses.A_BOLD)
        self.stdscr.addstr(1, 0, self.HELP[:w-1], curses.A_DIM)
        start_row = 3
        for i, (name, path, created) in enumerate(self.items):
            line = f" {name}  --  {created}   [{path.name}]"
            attr = curses.A_REVERSE if i == self.selected else curses.A_NORMAL
            if start_row + i < h - 2:
                self.stdscr.addstr(start_row + i, 0, line[:w-1], attr)
        if msg:
            self.stdscr.addstr(h-1, 0, msg[:w-1], curses.A_DIM)
        self.stdscr.refresh()
    def prompt(self, label: str) -> Optional[str]:
        h, w = self.stdscr.getmaxyx()
        win = curses.newwin(3, w-2, h-4, 1)
        win.border()
        win.addstr(0, 2, f" {label} ")
        tb = curses.textpad.Textbox(win.derwin(1, w-4, 1, 1))
        curses.curs_set(1)
        s = tb.edit().strip()
        curses.curs_set(0)
        return s or None
    def confirm(self, question: str) -> bool:
        s = self.prompt(question + " (y/N)")
        return (s or "").lower().startswith("y")
    def run(self) -> Tuple[Optional[Path], bool, Optional[str]]:
        msg = ""
        while True:
            self.draw(msg); msg = ""
            ch = self.stdscr.getch()
            if ch in (curses.KEY_UP, ord('k')):
                self.selected = max(0, self.selected - 1)
            elif ch in (curses.KEY_DOWN, ord('j')):
                self.selected = min(max(0, len(self.items)-1), self.selected + 1)
            elif ch in (10, 13):  # Enter
                if not self.items:
                    msg = "No states. Press 'n' to create one."
                else:
                    name, path, _ = self.items[self.selected]
                    return path, True, slugify(name)
            elif ch in (ord('n'), ord('N')):
                raw = self.prompt("New state name")
                if raw:
                    slug = slugify(raw)
                    newp = state_filename_for(slug)
                    atomic_write_bytes(newp, b'{}')  # placeholder so it shows immediately
                    os.chmod(newp, 0o600)
                    self.refresh_items()
                    self.selected = max(0, len(self.items)-1)
                    return newp, True, slug
            elif ch in (ord('d'), ord('D')):
                if not self.items:
                    msg = "Nothing to delete."
                else:
                    name, path, _ = self.items[self.selected]
                    if self.confirm(f"Delete '{path.name}'?"):
                        try:
                            path.unlink()
                            msg = f"Deleted {path.name}"
                            self.refresh_items()
                            self.selected = min(self.selected, max(0, len(self.items)-1))
                        except Exception as e:
                            msg = f"Delete failed: {e}"
            elif ch in (ord('q'), ord('Q')):
                return None, False, None

# ---------- storage helpers ----------
async def restore_storage_to_context(context, state_json: Dict[str, Any], session_id: str) -> None:
    cookies = state_json.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)
        log_json(session_id, "restore.cookies", count=len(cookies))
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
    origins = sorted(set(visited_origins))
    # cookies
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
    # localStorage
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
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        if not u.scheme or not u.netloc:
            return None
        return f"{u.scheme}://{u.netloc}"
    except Exception:
        return None

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

# ---------- core run ----------
async def run(args) -> int:
    session_id = ulid_like()
    log_json(session_id, "start", version=SCHEMA_VERSION)

    # Select/create via curses unless --state provided
    created_now = False
    state_name = None
    if args.state:
        p = Path(args.state)
        if p.suffix == "":
            state_name = slugify(p.name)
            candidate = STATE_DIR / f"{state_name}.state"
            state_path = candidate if candidate.exists() else state_filename_for(state_name)
            created_now = not candidate.exists()
        else:
            state_path = p
            state_name = slugify(p.stem.split("_")[0] if "_" in p.stem else p.stem)
            created_now = not state_path.exists()
    else:
        def _ui(stdscr):
            ui = CursesUI(stdscr)
            return ui.run()
        state_path, should_launch, state_name = curses.wrapper(_ui)
        if not should_launch or state_path is None:
            log_json(session_id, "exit", code=0, reason="user_cancel")
            return 0
        created_now = not state_path.exists() or state_path.stat().st_size == 0

    # Safety rail: refuse real Edge profile dirs
    risky_root = (Path.home() / "Library" / "Application Support" / "Microsoft Edge").resolve()
    if str(state_path.resolve()).lower().startswith(str(risky_root).lower()):
        log_json(session_id, "error", code="E_RISKY_PATH", path=str(state_path))
        print("Refusing to use a real Edge profile path. Choose a state under ./state/.")
        return 40

    # Encryption
    encryptor = Encryptor(enabled=not args.no_encrypt, use_keychain=not args.no_keychain, state_name=state_name or "state")

    # Load state
    init_state = None
    if state_path.exists() and state_path.stat().st_size > 0:
        try:
            blob = state_path.read_bytes()
            try:
                blob = encryptor.decrypt(blob)
            except Exception as e:
                log_json(session_id, "decrypt.error", error=str(e), path=str(state_path))
                print("Decryption failed. Wrong passphrase or missing Keychain item.")
                return 30
            init_state = json.loads(blob.decode("utf-8"))
            log_json(session_id, "state.loaded", path=str(state_path))
        except Exception as e:
            log_json(session_id, "state.load_error", error=str(e))
            init_state = None

    edge_exec = discover_edge_executable()
    extra_args = args.edge_arg or []

    async with async_playwright() as pw:
        # Non-persistent launch so Edge honors InPrivate
        if edge_exec:
            browser = await pw.chromium.launch(
                headless=False,
                executable_path=edge_exec,
                args=["--inprivate"] + extra_args,
            )
        else:
            browser = await pw.chromium.launch(
                headless=False,
                channel="msedge",
                args=["--inprivate"] + extra_args,
            )

        context = await browser.new_context()

        visited_origins: Set[str] = set()
        def track_frame_nav(frame):
            if frame.url:
                o = origin_of_url(frame.url)
                if o:
                    visited_origins.add(o)
        def attach_nav_tracker(p):
            p.on("framenavigated", track_frame_nav)
        context.on("page", attach_nav_tracker)

        # Restore storage
        if init_state:
            await restore_storage_to_context(context, init_state, session_id)

        page = await context.new_page()
        attach_nav_tracker(page)
        start_url = args.url or "https://www.microsoft.com/"
        await page.goto(start_url)

        # Wait until the last page closes (user quits)
        try:
            await page.wait_for_event("close")
        except Exception:
            pass

        # Dump storage
        state_json = await dump_storage_from_context(context, visited_origins, session_id)
        data = json.dumps(state_json, ensure_ascii=False).encode("utf-8")

        # Encrypt if needed
        if (state_json.get("cookies") or state_json.get("origins")) and not args.no_encrypt:
            try:
                blob = encryptor.encrypt(data)
            except Exception as e:
                log_json(session_id, "encrypt.error", error=str(e))
                print("Encryption failed; state will NOT be written.")
                blob = None
        else:
            blob = data

        if blob is not None:
            if created_now and state_path.parent.resolve() == STATE_DIR.resolve():
                if not re.search(r"_\d{10}(-\d+)?\.state$", state_path.name):
                    state_path = state_filename_for(state_name or "state")
            atomic_write_bytes(state_path, blob)
            os.chmod(state_path, 0o600)
            log_json(session_id, "state.saved", path=str(state_path), bytes=len(blob))

        await context.close()
        await browser.close()

    log_json(session_id, "exit", code=0)
    return 0

def build_arg_parser():
    ap = argparse.ArgumentParser(description="CLI curses Edge launcher with state select/create/delete in ./state/.")
    ap.add_argument("--state", help="Optional: state name or path (bypasses UI).")
    ap.add_argument("--url", default="https://teams.microsoft.com", help="Initial URL to open.")
    ap.add_argument("--edge-arg", action="append", default=[], help="Additional flags to pass to Edge.")
    ap.add_argument("--no-encrypt", action="store_true", help="Disable encryption at rest (not recommended).")
    ap.add_argument("--no-keychain", action="store_true", help="Disable macOS Keychain usage for state secrets.")
    return ap

def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        rc = asyncio.run(run(args))  # CLI-only: assumes no running event loop
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)

if __name__ == "__main__":
    main()