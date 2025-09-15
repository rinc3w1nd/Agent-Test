import json
from typing import List, Dict

class Corpus:
    def __init__(self):
        self.items: List[Dict] = []
        self.i = 0

    def load_jsonl(self, text: str) -> int:
        self.items = []
        self.i = 0
        lines = [ln for ln in (text or "").splitlines() if ln.strip()]
        if not lines:
            return 0
        # Try JSONL first
        ok = False
        for line in lines:
            try:
                obj = json.loads(line)
                self.items.append(obj)
                ok = True
            except Exception:
                ok = False
                break
        if not ok:
            # Try single JSON array/object
            try:
                obj = json.loads(text)
                if isinstance(obj, list):
                    self.items = obj
                else:
                    self.items = [obj]
            except Exception:
                self.items = []
        return len(self.items)

    def current(self):
        if 0 <= self.i < len(self.items):
            return self.items[self.i]
        return None

    def next(self):
        if self.i + 1 < len(self.items):
            self.i += 1
            return True
        return False

    def prev(self):
        if self.i - 1 >= 0:
            self.i -= 1
            return True
        return False