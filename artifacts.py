from pathlib import Path
from typing import Dict
from .utils import sanitize_id, append_atomic

# --- replace existing append_text with this ---
def append_text(run_ts: str, item_id: str, meta: Dict, text: str, text_dir: str,
                reply_detected: bool = False, reply_len: int = 0, operator_note: str = "") -> Path:
    safe = sanitize_id(item_id)
    p = Path(text_dir) / f"{safe}.txt"
    order = ["id","family","locale","evasion","goal","expected_outcome","threat_model","defender_context","source"]
    meta_line = "[{}]\n".format(" ".join(f"{k}={meta.get(k,'')}" for k in order))
    block = (
        f"=== RUN {run_ts} ===\n"
        + meta_line
        + "[BOT REPLY - plain text extraction]\n"
        + (text or "") + "\n"
        + f"--- operator_note: {operator_note or 'none'}\n"
        + f"--- reply_detected: {str(bool(reply_detected)).lower()}\n"
        + f"--- reply_len_chars: {int(reply_len)}\n\n"
    )
    append_atomic(p, block)
    return p

def append_html(run_ts: str, item_id: str, html: str, html_dir: str, max_bytes: int = 5_000_000) -> Path:
    """
    Append a run block to artifacts/html/<id>.html:
      <!-- RUN <ts> -->
      <div class="message-snapshot"> ...raw innerHTML... </div>

    Enforces a size cap with truncation notice to prevent massive files (NFR size safety).
    """
    safe = sanitize_id(item_id)
    p = Path(html_dir) / f"{safe}.html"
    data = html or ""
    if len(data.encode("utf-8")) > max_bytes:
        half = max_bytes // 2
        data = data[:half] + "\n<!-- TRUNCATED -->\n" + data[-half:]
    block = f"<!-- RUN {run_ts} -->\n<div class=\"message-snapshot\">\n{data}\n</div>\n\n"
    append_atomic(p, block)
    return p

async def screenshot(ts_action: str, item_id: str, page, screens_dir: str) -> Path:
    """
    Save artifacts/screens/<id>.<YYMMDD-HHMMSS>.png using the *action-time* timestamp (NFR D2).
    """
    safe = sanitize_id(item_id)
    p = Path(screens_dir) / f"{safe}.{ts_action}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(p), full_page=True)
    except Exception:
        # Non-fatal: audit layer should log outcomes; artifact write attempts are best-effort.
        pass
    return p