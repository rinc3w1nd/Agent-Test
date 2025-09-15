from datetime import datetime, timezone

def now_ts_run() -> str:
    """UTC timestamp for filenames: yyMMdd_HHmmss"""
    return datetime.now(timezone.utc).strftime("%y%m%d_%H%M%S")