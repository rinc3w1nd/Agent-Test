#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import curses
import json
import logging
import os
import textwrap
import time
from pathlib import Path
from typing import List, Optional

# ---------- Config handling (YAML with fallback) ----------

DEFAULTS = {
    "app_title": "Edge InPrivate Launcher (curses)",
    "default_url": "https://www.bing.com",
    "state_dir": "./state",
    "log_dir": "./logs",
    "log_file": "edge_inprivate_curses.log",
    "channels": ["msedge", "chrome", "chromium"],
    "headless": False,
    "inprivate_arg": "--inprivate",
    # watchdog: 0.4s * ticks (~4 minutes default 600)
    "idle_watchdog_ticks": 600
}

def load_yaml_config(path: Path) -> dict:
    cfg = DEFAULTS.copy()
    if not path.exists():
        return cfg
    try:
        try:
            import yaml  # type: ignore
        except Exception:
            # Minimalistic "good enough" YAML reader for flat keys (string/list/bool/int)
            raw = path.read_text(encoding="utf-8")
            temp = {}
            for line in raw.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if ":" not in s:
                    continue
                key, val = s.split(":", 1)
                key = key.strip()
                val = val.strip()
                # lists like [a, b, c]
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    if inner:
                        temp[key] = [v.strip().strip("'\"") for v in inner.split(",")]
                    else:
                        temp[key] = []
                elif val.lower() in ("true", "false"):
                    temp[key] = val.lower() == "true"
                else:
                    # try int
                    try:
                        temp[key] = int(val)
                    except ValueError:
                        temp[key] = val.strip("'\"")
            cfg.update(temp)
            return cfg
        else:
            with path.open("r", encoding="utf-8") as f:
                y = yaml.safe_load(f) or {}
                if not isinstance(y, dict):
                    return cfg
                cfg.update(y)
                return cfg
    except Exception:
        # On any parse failure, fall back silently to defaults
        return cfg

# ---------- Paths & logging ----------

ROOT = Path(os.getcwd())
CFG_PATH = ROOT / "config.yaml"
CFG = load_yaml_config(CFG_PATH)

APP_TITLE = CFG["app_title"]
STATE_DIR = ROOT / CFG["state_dir"]
LOG_DIR = ROOT / CFG["log_dir"]
LOG_FILE = LOG_DIR / CFG["log_file"]
DEFAULT_URL = CFG["default_url"]
CHANNELS = list(CFG["channels"])
HEADLESS = bool(CFG["headless"])
INPRIVATE_ARG = str(CFG["inprivate_arg"])
IDLE_WATCHDOG_TICKS = int(CFG["idle_watchdog_ticks"])

STATE_EXT = ".json"

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------- Playwright helpers ----------

def _launch_browser_inprivate():
    """
    Launch a Chromium-based browser with --inprivate (or equivalent) and return (browser, pw, channel).
    """
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    last_err = None
    for ch in CHANNELS:
        try:
            logging.info(f"Attempting channel={ch} (InPrivate)")
            if ch in ("chromium", None):
                browser = pw.chromium.launch(headless=HEADLESS, args=[INPRIVATE_ARG])
            else:
                browser = pw.chromium.launch(headless=HEADLESS, channel=ch, args=[INPRIVATE_ARG])
            return browser, pw, ch
        except Exception as e:
            last_err = e
            logging.warning(f"Launch failed for channel={ch}: {e}")
            continue
    pw.stop()
    raise RuntimeError(f"Failed to launch any browser channel. Last error: {last_err}")

def _atomic_write_json(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)

# ---------- State ops ----------

def sanitize_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join(("_" if ch in bad else ch) for ch in name).strip()

def list_states() -> List[Path]:
    return sorted([p for p in STATE_DIR.glob(f"*{STATE_EXT}") if p.is_file()])

def delete_state(state_path: Path) -> Optional[str]:
    try:
        state_path.unlink(missing_ok=False)
        logging.info(f"Deleted state {state_path}")
        return None
    except Exception as e:
        logging.exception("Delete state failed")
        return str(e)

# ---------- Curses UI ----------

class UIState:
    def __init__(self):
        self.url = DEFAULT_URL
        self.states = list_states()
        self.selected = 0 if self.states else -1
        self.message = ""

    def refresh(self):
        self.states = list_states()
        if not self.states:
            self.selected = -1
        else:
            self.selected = max(0, min(self.selected, len(self.states) - 1))

def draw_menu(stdscr, ui: UIState):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    title = f"{APP_TITLE}"
    stdscr.addstr(0, 0, title[:w-1], curses.A_BOLD)

    help1 = "[Enter/L] Launch  (u) URL  (n) New  (d) Delete  (r) Refresh  (q) Quit"
    stdscr.addstr(1, 0, help1[:w-1], curses.A_DIM)

    url_line = f"URL: {ui.url}"
    stdscr.addstr(3, 0, url_line[:w-1])

    stdscr.addstr(5, 0, "States (JSON):", curses.A_UNDERLINE)
    start_row = 6
    for idx, p in enumerate(ui.states):
        marker = "➤" if idx == ui.selected else " "
        name = p.name
        line = f" {marker} {name}"
        if idx == ui.selected:
            stdscr.addstr(start_row + idx, 0, line[:w-1], curses.A_REVERSE)
        else:
            stdscr.addstr(start_row + idx, 0, line[:w-1])

    if ui.message:
        stdscr.addstr(h-1, 0, ui.message[:w-1], curses.A_DIM)

    stdscr.refresh()

def prompt(stdscr, prompt_text: str, default: str = "") -> Optional[str]:
    curses.echo()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h-2, 0, " " * (w-1))
    stdscr.addstr(h-2, 0, f"{prompt_text} [{default}]: ")
    stdscr.refresh()
    try:
        s = stdscr.getstr(h-2, len(prompt_text) + 3 + len(default), 512)
        s = s.decode("utf-8").strip()
        curses.noecho()
        if not s and default:
            return default
        return s
    except KeyboardInterrupt:
        curses.noecho()
        return None

def confirm(stdscr, text: str) -> bool:
    curses.echo()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h-2, 0, " " * (w-1))
    stdscr.addstr(h-2, 0, f"{text} (y/N): ")
    stdscr.refresh()
    try:
        s = stdscr.getstr(h-2, len(text) + 7, 4).decode("utf-8").strip().lower()
        curses.noecho()
        return s == "y"
    except KeyboardInterrupt:
        curses.noecho()
        return False

# ---------- Session Panel ----------

def session_panel(stdscr, state_file: Optional[Path], browser, context, pw, channel, url: str):
    """
    Live panel that runs while Edge is open. Keys:
      S: Save & Exit
      X: Discard & Exit
    Auto-save on browser disconnect or all pages closed.
    """
    h, w = stdscr.getmaxyx()
    title = f"[Running: {channel or 'chromium'} | state={state_file.name if state_file else '(ephemeral)'} | url={url}]"
    info  = "S: Save & Exit   X: Discard & Exit   (auto-save on window close)"
    stdscr.clear()
    try:
        stdscr.addstr(0, 0, title[:w-1], curses.A_BOLD)
        stdscr.addstr(2, 0, info[:w-1])
    except curses.error:
        pass
    stdscr.refresh()

    # Prepare detection
    disconnected = False
    def _on_disconnect():
        nonlocal disconnected
        disconnected = True

    browser.on("disconnected", lambda: _on_disconnect())

    # Event loop: watch pages + keys
    idle_ticks = 0
    stdscr.nodelay(True)  # nonblocking getch
    saved = False
    discard = False

    def all_pages_closed_now():
        try:
            return all(p.is_closed() for p in list(context.pages)) or len(context.pages) == 0
        except Exception:
            return True

    while True:
                # Re-arm curses modes each iteration
        stdscr.keypad(True)
        curses.noecho()
        curses.cbreak()
# Input
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            ch = -1

        if ch in (ord('s'), ord('S')):
            # Save & Exit
            if state_file:
                try:
                    data = context.storage_state()
                    _atomic_write_json(state_file, data)
                    logging.info(f"[panel] Saved storage_state to {state_file}")
                    saved = True
                except Exception as e:
                    logging.error(f"[panel] Save failed: {e}")
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
            break

        if ch in (ord('x'), ord('X')):
            # Discard & Exit
            discard = True
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
            break

        # Auto conditions
        if disconnected or all_pages_closed_now():
            # Auto-save if we have a state_file, unless user picked discard
            if state_file and not discard:
                try:
                    data = context.storage_state()
                    _atomic_write_json(state_file, data)
                    logging.info(f"[panel] Auto-saved storage_state to {state_file}")
                    saved = True
                except Exception as e:
                    logging.error(f"[panel] Auto-save failed: {e}")
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
            break

        # Idle watchdog (phantom pages)
        try:
            vis_pages = [p for p in list(context.pages) if not p.is_closed()]
            visible = False
            for p in vis_pages:
                try:
                    if p.url and not p.url.startswith("devtools:"):
                        visible = True
                        break
                except Exception:
                    pass
            if not visible:
                idle_ticks += 1
            else:
                idle_ticks = 0
        except Exception:
            idle_ticks += 1

        if idle_ticks > IDLE_WATCHDOG_TICKS:
            # Treat as auto-close
            if state_file and not discard:
                try:
                    data = context.storage_state()
                    _atomic_write_json(state_file, data)
                    logging.info(f"[panel] Watchdog auto-saved storage_state to {state_file}")
                    saved = True
                except Exception as e:
                    logging.error(f"[panel] Watchdog auto-save failed: {e}")
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
            break

        time.sleep(0.1)

    stdscr.nodelay(False)
    return saved, discard

# ---------- Launch flows ----------

def launch_with_state(state_file: Optional[Path], url: str) -> Optional[str]:
    """
    Launch InPrivate, preload storage_state from JSON if provided.
    Session Panel handles save/discard/auto.
    """
    try:
        from playwright.sync_api import Error as PWError  # noqa: F401
        browser, pw, channel = _launch_browser_inprivate()
        logging.info(f"Launched channel={channel} (InPrivate)")

        context_kwargs = {}
        if state_file and state_file.exists():
            context_kwargs["storage_state"] = str(state_file)
            logging.info(f"Preloading storage_state from {state_file}")

        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        try:
            page.goto(url or DEFAULT_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logging.warning(f"Navigation warning: {e}")

        return browser, pw, channel, context  # Caller drives the session panel
    except Exception as e:
        logging.exception("Launch failed")
        return str(e)

def create_state_interactive(state_name: str, start_url: str) -> Optional[str]:
    """
    Create a brand-new state JSON by launching InPrivate blank, logging in,
    and then saving via the Session Panel or auto-close.
    """
    state_file = STATE_DIR / (sanitize_filename(state_name) + STATE_EXT)
    if state_file.exists():
        return f"State already exists: {state_file.name}"

    try:
        from playwright.sync_api import Error as PWError  # noqa: F401
        browser, pw, channel = _launch_browser_inprivate()
        logging.info(f"[create] Launched channel={channel} (InPrivate)")

        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(start_url or DEFAULT_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logging.warning(f"[create] Navigation warning: {e}")

        return browser, pw, channel, context, state_file
    except Exception as e:
        logging.exception("Create-state failed")
        return str(e)

# ---------- App loop ----------

def end_curses_and_call(fn, *args, **kwargs):
    curses.endwin()
    try:
        return fn(*args, **kwargs)
    finally:
        stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)

def main_loop(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    ui = UIState()

    while True:
        ui.refresh()
        draw_menu(stdscr, ui)
        ch = stdscr.getch()

        if ch in (curses.KEY_UP, ord('k')):
            if ui.states:
                ui.selected = (ui.selected - 1) % len(ui.states)

        elif ch in (curses.KEY_DOWN, ord('j')):
            if ui.states:
                ui.selected = (ui.selected + 1) % len(ui.states)

        elif ch in (ord('l'), ord('L'), curses.KEY_ENTER, 10, 13):
            state_path = ui.states[ui.selected] if (ui.selected >= 0 and ui.states) else None
            ui.message = "Launching InPrivate…"
            draw_menu(stdscr, ui)
            res = end_curses_and_call(launch_with_state, state_path, ui.url)
            if isinstance(res, str):
                ui.message = f"ERROR: {res}"
            else:
                browser, pw, channel, context = res
                # Enter session panel
                saved, discarded = session_panel(stdscr, state_path, browser, context, pw, channel, ui.url)
                # Reset curses input modes
                curses.flushinp()
                stdscr.keypad(True)
                curses.noecho()
                curses.cbreak()
                # Reset curses input modes
                curses.flushinp()
                stdscr.keypad(True)
                curses.noecho()
                curses.cbreak()
                if saved:
                    ui.message = f"Saved: {state_path.name if state_path else '(ephemeral)'}"
                elif discarded:
                    ui.message = "Discarded."
                else:
                    ui.message = "Session ended."
            # loop continues back to menu

        elif ch in (ord('n'), ord('N')):
            name = prompt(stdscr, "New state name", "session")
            if name is None or not name.strip():
                ui.message = "Create canceled."
            else:
                name = sanitize_filename(name)
                start_url = prompt(stdscr, "Start at URL", ui.url) or ui.url
                ui.message = "Creating state…"
                draw_menu(stdscr, ui)
                res = end_curses_and_call(create_state_interactive, name, start_url)
                if isinstance(res, str):
                    ui.message = f"ERROR: {res}"
                else:
                    browser, pw, channel, context, state_file = res
                    # Use session panel; it will write to state_file on save/auto
                    saved, discarded = session_panel(stdscr, state_file, browser, context, pw, channel, start_url)
                # Reset curses input modes
                curses.flushinp()
                stdscr.keypad(True)
                curses.noecho()
                curses.cbreak()
                # Reset curses input modes
                curses.flushinp()
                stdscr.keypad(True)
                curses.noecho()
                curses.cbreak()
                    if saved:
                        ui.message = f"Saved: {state_file.name}"
                    elif discarded:
                        # If discarded, ensure no file left behind
                        try:
                            if state_file.exists():
                                state_file.unlink()
                        except Exception:
                            pass
                        ui.message = "Discarded."
                    else:
                        ui.message = "Session ended."
                ui.refresh()

        elif ch in (ord('d'), ord('D')):
            if ui.selected >= 0 and ui.states:
                state_path = ui.states[ui.selected]
                if confirm(stdscr, f"Delete {state_path.name}?"):
                    err = delete_state(state_path)
                    if err:
                        ui.message = f"ERROR: {err}"
                    else:
                        ui.message = f"Deleted {state_path.name}"
            else:
                ui.message = "No state selected."

        elif ch in (ord('u'), ord('U')):
            new_url = prompt(stdscr, "Set URL", ui.url)
            if new_url is not None and new_url.strip():
                ui.url = new_url.strip()
                ui.message = f"URL set to {ui.url}"

        elif ch in (ord('r'), ord('R')):
            ui.refresh()
            ui.message = "Refreshed."

        elif ch in (ord('q'), ord('Q'), 27):
            break

        else:
            pass

def main():
    try:
        import playwright  # noqa: F401
    except Exception as e:
        print("Playwright is required. Install with: pip install playwright && playwright install")
        raise

    curses.wrapper(main_loop)

if __name__ == "__main__":
    main()
