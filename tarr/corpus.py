import json
from typing import List, Dict, Optional

REQUIRED_KEYS = ["id","family","locale","evasion","goal","expected_outcome","payload","threat_model","defender_context","source"]

class Corpus:
    def __init__(self):
        self.items: List[Dict] = []
        self.i = 0

    def load_jsonl(self, text: str) -> int:
        self.items = []
        self.i = 0
        for line in (text or "").splitlines():
            line = line.strip()
            if not line: continue
            try:
                obj = json.loads(line)
                for k in REQUIRED_KEYS:
                    obj.setdefault(k, "")
                self.items.append(obj)
            except Exception:
                continue
        return len(self.items)

    def current(self) -> Optional[Dict]:
        if 0 <= self.i < len(self.items):
            return self.items[self.i]
        return None

    def next(self) -> bool:
        if self.i + 1 < len(self.items):
            self.i += 1
            return True
        return False

    def prev(self) -> bool:
        if self.i - 1 >= 0:
            self.i -= 1
            return True
        return False