import json
from pathlib import Path
from .utils import now_ts_run

class Audit:
    def __init__(self, run_ts: str, script: str, out_dir: str):
        self.run_ts = run_ts
        self.script = script
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.out_dir / f"{script}.{run_ts}.log"

    def log(self, event: str, **kv):
        rec = {"ts": now_ts_run(), "event": event}
        rec.update(kv)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def open_audit(run_ts: str, script: str, out_dir: str) -> Audit:
    return Audit(run_ts, script, out_dir)