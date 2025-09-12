
#!/usr/bin/env python3
"""
Edge State Launcher (macOS-focused) — Full Corrected, Loop-Safe
----------------------------------------------------------------
- InPrivate mandatory (--inprivate) + temp --user-data-dir
- Pick existing state (searchable UI) or create new state name
- New states saved on exit to StateName_YYMMDDHHMM.state
- Restore cookies + localStorage on launch (simulated persistence)
- Logs JSONL to ./logs/
- Encryption at rest (AES-GCM + Argon2id; uses macOS Keychain if available, else passphrase)
- Safety rail: refuses real Edge profile dirs
- Loop-safe main(): works from plain CLI and from environments with a running asyncio loop (e.g., IPython/Jupyter) by running in a dedicated thread.
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
STATES_DIR = BASE_DIR / "states"
LOGS_DIR = BASE_DIR / "logs"
SCHEMA_VERSION = 1

STATES_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------- Utilities -------------------------

def ts_stamp() -> str:
    return datetime.now().strftime("%y%m%d%H%M")  # local 24h

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

# ------------------------- Encryption helpers -------------------------

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
        return blob  # legacy plaintext

# ------------------------- TUI -------------------------

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
            # fallback: treat as new state name
            slug = slugify(choice or "state")
            path = state_filename_for(slug)
            return path, True, slug
        if idx == 0:
            slug = slugify("state")
            path = state_filename_for(slug)
            return path, True, slug
        name, fn, _ = items[idx - 1]
        return STATES_DIR / fn, False, slugify(name)

# ------------------------- Storage helpers -------------------------

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
    if args.state:
        p = Path(args.state)
        if p.suffix == "":
            state_name = slugify(p.name)
            candidate = STATES_DIR / f"{state_name}.state"
            state_path = candidate if candidate.exists() else state_filename_for(state_name)
            created_now = not candidate.exists()
        else:
            state_path = p
            state_name = slugify(p.stem.split("_")[0] if "_" in p.stem else p.stem)
            created_now = not state_path.exists()
    else:
        state_path, created_now, state_name = pick_or_create_state_interactive()

    # Safety rail
    risky_root = (Path.home() / "Library" / "Application Support" / "Microsoft Edge").resolve()
    if str(state_path.resolve()).lower().startswith(str(risky_root).lower()):
        log_json(session_id, "error", code="E_RISKY_PATH", path=str(state_path))
        print("Refusing to use a real Edge profile path. Choose a state under ./states/.")
        return 40

    # Encryption
    encryptor = Encryptor(enabled=not args.no_encrypt, use_keychain=not args.no_keychain, state_name=state_name or "state")

    # Load state
    init_state = None
    if state_path.exists():
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

    # Launch Edge (always InPrivate)
    edge_exec = discover_edge_executable()
    launch_args = ["--inprivate"]
    with tempfile.TemporaryDirectory(prefix="edge-np-") as tmpdir:
        launch_args.append(f"--user-data-dir={tmpdir}")
        if args.edge_arg:
            launch_args.extend(args.edge_arg)

        async with async_playwright() as pw:
            # Prefer Edge binary; else try Playwright channel=msedge; else vanilla Chromium
            launch_kwargs = dict(headless=False, args=launch_args)
            if edge_exec:
                launch_kwargs["executable_path"] = edge_exec
            else:
                launch_kwargs["channel"] = "msedge"

            browser = await pw.chromium.launch(**launch_kwargs)
            context = await browser.new_context()

            visited_origins: Set[str] = set()

            def track_frame_nav(frame):
                if frame.url:
                    o = origin_of_url(frame.url)
                    if o:
                        visited_origins.add(o)

            def attach_nav_tracker(p):
                p.on("framenavigated", track_frame_nav)

            # Attach to future pages
            context.on("page", attach_nav_tracker)

            # Restore storage
            if init_state:
                await restore_storage_to_context(context, init_state, session_id)

            # First page: attach tracking BEFORE navigation
            page = await context.new_page()
            attach_nav_tracker(page)
            start_url = args.url or "https://www.microsoft.com/"
            await page.goto(start_url)

            # Wait for window close
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

            # Write state
            if blob is not None:
                if created_now and state_path.parent.resolve() == STATES_DIR.resolve():
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
    ap = argparse.ArgumentParser(description="Launch Edge InPrivate with a selected state; create/save state on exit if new.")
    ap.add_argument("--state", help="State name or path. If omitted, shows a picker.")
    ap.add_argument("--url", default="https://teams.microsoft.com", help="Initial URL to open.")
    ap.add_argument("--edge-arg", action="append", default=[], help="Additional flags to pass to Edge.")
    ap.add_argument("--no-encrypt", action="store_true", help="Disable encryption at rest (not recommended).")
    ap.add_argument("--no-keychain", action="store_true", help="Disable macOS Keychain usage for state secrets.")
    return ap

def _run_coro_loopsafe(coro):
    """Run an async coroutine even if a loop is already running (e.g., IPython).
    Uses a dedicated thread to call asyncio.run(coro).
    """
    from threading import Thread
    result = {"rc": 1, "err": None}
    def runner():
        try:
            result["rc"] = asyncio.run(coro)
        except Exception as e:
            result["err"] = e
    t = Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if result["err"]:
        raise result["err"]
    return result["rc"]

def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        try:
            # If a loop is already running (IPython/Jupyter), avoid asyncio.run directly.
            asyncio.get_running_loop()
            rc = _run_coro_loopsafe(run(args))
        except RuntimeError:
            # No running loop → safe to use asyncio.run
            rc = asyncio.run(run(args))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)

if __name__ == "__main__":
    main()
