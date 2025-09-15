from pathlib import Path
from typing import Dict, Any
from .utils import now_ts_run

def _ensure_dir(p: str) -> Path:
    path = Path(p)
    path.mkdir(parents=True, exist_ok=True)
    return path

def append_text(run_ts: str, rid: str, row: Dict[str,Any], reply_text: str, out_dir: str, **meta) -> Path:
    _ensure_dir(out_dir)
    stem = f"{rid}.{run_ts}.txt"
    p = Path(out_dir) / stem
    with open(p, "a", encoding="utf-8") as f:
        f.write(reply_text or "")
        f.write("\n")
    return p

def append_html(run_ts: str, rid: str, html: str, out_dir: str) -> Path:
    _ensure_dir(out_dir)
    stem = f"{rid}.{run_ts}.html"
    p = Path(out_dir) / stem
    with open(p, "w", encoding="utf-8") as f:
        f.write(html or "")
    return p

async def screenshot(ts: str, rid: str, page, out_dir: str) -> str:
    _ensure_dir(out_dir)
    stem = f"{rid}.{ts}.png"
    p = str((Path(out_dir) / stem).resolve())
    await page.screenshot(path=p, full_page=True)
    return p