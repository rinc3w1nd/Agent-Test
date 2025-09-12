
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import curses
import json
import logging
import textwrap
import time
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import sync_playwright, Error as PWError

APP_TITLE = "Edge InPrivate Launcher (curses) — storage_state only"
STATE_DIR = Path("./state")
LOG_DIR = Path("./logs")
LOG_FILE = LOG_DIR / "edge_inprivate_curses.log"
DEFAULT_URL = "https://www.bing.com"
STATE_EXT = ".json"

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def sanitize_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    name = "".join(("_" if ch in bad else ch) for ch in name)
    return name.strip()

def list_states() -> List[Path]:
    return sorted([p for p in STATE_DIR.glob(f"*{STATE_EXT}") if p.is_file()])

def _launch_browser(channel_candidates=("msedge", "chrome", "chromium")):
    """
    Launch a Chromium-based browser with --inprivate.
    Returns (browser, playwright_instance, chosen_channel) or raises.
    """
    pw = sync_playwright().start()
    last_err = None
    for ch in channel_candidates:
        try:
            logging.info(f"Attempting channel={ch} (InPrivate)")
            if ch in ("chromium", None):
                browser = pw.chromium.launch(headless=False, args=["--inprivate"])
            else:
                browser = pw.chromium.launch(headless=False, channel=ch, args=["--inprivate"])
            return browser, pw, ch
        except Exception as e:
            last_err = e
            logging.warning(f"Launch failed for channel={ch}: {e}")
            continue
    pw.stop()
    raise RuntimeError(f"Failed to launch any browser channel. Last error: {last_err}")

def launch_with_state(state_file: Optional[Path], url: str) -> Optional[str]:
    """
    Launch InPrivate context and optionally preload storage_state from JSON.
    On close, save updated storage_state back to state_file (if provided).
    """
    try:
        browser, pw, channel = _launch_browser()
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

        # Robust wait: return when (a) all pages are closed, or (b) browser disconnects
        # Also handle stealth/background pages by listening to page 'close' events.
        all_closed = False
        disconnected = False

        def _on_disconnect():
            nonlocal disconnected
            disconnected = True

        def _all_pages_closed_now():
            return all(p.is_closed() for p in list(context.pages)) or len(context.pages) == 0

        browser.on("disconnected", lambda: _on_disconnect())
        for p in list(context.pages):
            p.on("close", lambda _: None)

        idle_ticks = 0
        while True:
            if disconnected or _all_pages_closed_now():
                break
            # If pages exist but none are visible & have about:blank, consider idle
            try:
                vis_pages = [p for p in list(context.pages) if not p.is_closed()]
                visible = False
                for p in vis_pages:
                    try:
                        # Ignore pages that are just about:blank or devtools
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
                pass
            if idle_ticks > 600:  # ~4 minutes at 0.4s
                break
            time.sleep(0.4)

        # Save storage_state back (only if we have a file target)
        if state_file:
            try:
                context.storage_state(path=str(state_file))
                logging.info(f"Saved storage_state to {state_file}")
            except Exception as e:
                logging.error(f"Failed saving storage_state: {e}")

        context.close()
        browser.close()
        pw.stop()
        return None
    except Exception as e:
        logging.exception("Launch failed")
        return str(e)

def create_state_interactive(state_name: str, start_url: str) -> Optional[str]:
    """
    Create a *new* state JSON by launching InPrivate with a blank context,
    letting the user log in, then saving storage_state to ./state/<name>.json
    when they close the window.
    """
    state_file = STATE_DIR / (sanitize_filename(state_name) + STATE_EXT)
    if state_file.exists():
        return f"State already exists: {state_file.name}"

    try:
        browser, pw, channel = _launch_browser()
        logging.info(f"[create] Launched channel={channel} (InPrivate)")

        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(start_url or DEFAULT_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logging.warning(f"[create] Navigation warning: {e}")

        # Robust wait during creation as well
        disconnected = False

        def _on_disconnect():
            nonlocal disconnected
            disconnected = True

        browser.on("disconnected", lambda: _on_disconnect())

        idle_ticks = 0
        while True:
            try:
                if disconnected or all(p.is_closed() for p in list(context.pages)) or len(context.pages) == 0:
                    break
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
                pass
            if idle_ticks > 600:
                break
            time.sleep(0.4)

        # Save storage_state to file
        try:
            context.storage_state(path=str(state_file))
            logging.info(f"[create] Saved new storage_state to {state_file}")
        except Exception as e:
            logging.error(f"[create] Failed to save storage_state: {e}")
            raise

        context.close()
        browser.close()
        pw.stop()
        return None
    except Exception as e:
        logging.exception("Create-state failed")
        return str(e)

def delete_state(state_path: Path) -> Optional[str]:
    try:
        state_path.unlink(missing_ok=False)
        logging.info(f"Deleted state {state_path}")
        return None
    except Exception as e:
        logging.exception("Delete state failed")
        return str(e)

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

def end_curses_and_call(fn, *args, **kwargs):
    curses.endwin()
    try:
        return fn(*args, **kwargs)
    finally:
        # Re-init minimal curses so loop can resume
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
            err = end_curses_and_call(launch_with_state, state_path, ui.url)
            if err:
                ui.message = f"ERROR: {err}"
            else:
                ui.message = "Session ended."
        elif ch in (ord('n'), ord('N')):
            name = prompt(stdscr, "New state name", "session")
            if name is None or not name.strip():
                ui.message = "Create canceled."
            else:
                name = sanitize_filename(name)
                start_url = prompt(stdscr, "Start at URL", ui.url) or ui.url
                ui.message = "Creating state… close the browser to save."
                draw_menu(stdscr, ui)
                err = end_curses_and_call(create_state_interactive, name, start_url)
                if err:
                    ui.message = f"ERROR: {err}"
                else:
                    ui.message = f"Saved: {name}{STATE_EXT}"
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
        elif ch in (ord('q'), ord('Q'), 27):  # ESC or q
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
