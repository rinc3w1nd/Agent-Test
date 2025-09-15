#!/usr/bin/env python3
# pip install pyobjc
import json, sys
from pathlib import Path
from AppKit import NSPasteboard

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "capture.clip.json")

pb = NSPasteboard.generalPasteboard()
items = pb.pasteboardItems()
if not items:
    print("Clipboard is empty", file=sys.stderr)
    sys.exit(1)

item = items[0]
payload = {}
# Teams usually keys off text/html; text/plain is a good fallback
HTML = "public.html"
PLAIN = "public.utf8-plain-text"

def get_str(item, uti):
    d = item.dataForType_(uti)
    if not d: return None
    return bytes(d).decode("utf-8", errors="replace")

payload["text/html"]  = get_str(item, HTML)
payload["text/plain"] = get_str(item, PLAIN)

# Keep any other string-y types too (just in case)
for uti in item.types():
    if uti in (HTML, PLAIN): 
        continue
    d = item.dataForType_(uti)
    if not d: 
        continue
    try:
        txt = bytes(d).decode("utf-8")
        payload.setdefault("extra", {})[uti] = txt
    except UnicodeDecodeError:
        # binary/custom types generally can't be replayed via synthetic paste
        pass

with OUT.open("w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(f"Wrote {OUT.resolve()}")