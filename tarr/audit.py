from pathlib import Path
from datetime import datetime
from .utils import now_ts_minute, append_atomic

class Audit:
    """Append-only, atomic audit logger with HH:mm:ss.SSS timestamps."""
    def __init__(self, path: Path):
        self.path = path

    def log(self, action: str, **kv) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:mm:ss.SSS
        parts = [f"{ts} [{action.upper()}]"]
        for k, v in kv.items():
            parts.append(f"{k}={v}")
        line = " ".join(parts) + "\n"
        append_atomic(self.path, line)

def open_audit(run_ts: str, script_name: str, audit_dir: str) -> Audit:
    """
    Create per-run audit file: audit/<script-name>-yyMMdd_HHMM.txt,
    write a BOOT line, and return an Audit handle.
    """
    p = Path(audit_dir) / f"{script_name}-{now_ts_minute()}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    aud = Audit(p)
    aud.log("BOOT", run_ts=run_ts, script=script_name)
    return aud