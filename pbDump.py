#!/usr/bin/env python3
# macOS pasteboard inspector: lists all clipboard types (UTIs) and previews/saves payloads
# Requires: pip install pyobjc

import argparse
import base64
import binascii
import sys
import textwrap
from pathlib import Path

from AppKit import NSPasteboard
from Foundation import NSData

# Common UTI -> reasonable file extension guesses
EXT_MAP = {
    "public.utf8-plain-text": "txt",
    "public.utf16-plain-text": "txt",
    "public.rtf": "rtf",
    "public.html": "html",
    "public.url": "url",
    "public.pdf": "pdf",
    "public.png": "png",
    "public.jpeg": "jpg",
    "public.json": "json",
    "public.xml": "xml",
    "public.plain-text": "txt",
    # Teams often uses custom UTIs; fall back to .bin for unknowns
}

TEXTY_UTIS = {
    "public.utf8-plain-text",
    "public.utf16-plain-text",
    "public.plain-text",
    "public.rtf",
    "public.html",
    "public.json",
    "public.xml",
}

def sanitize(s: str) -> str:
    keep = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        else:
            keep.append("_")
    sanitized = "".join(keep).strip("_")
    return sanitized or "unknown"

def guess_ext(uti: str) -> str:
    return EXT_MAP.get(uti, "bin")

def bytes_preview(b: bytes, max_chars: int = 600) -> str:
    # Try UTF-8 (lossy) to catch hidden JSON/HTML/RTF; else hex
    try:
        txt = b.decode("utf-8", errors="replace")
        # If the text looks mostly binary (lots of replacement chars), use hex preview
        replacement_ratio = txt.count("\uFFFD") / max(1, len(txt))
        if replacement_ratio < 0.10:
            # Collapse very long lines for terminal sanity
            snippet = txt[:max_chars]
            return snippet if len(txt) <= max_chars else snippet + "\n… [truncated]"
    except Exception:
        pass
    # Fallback to hex
    hx = binascii.hexlify(b[:256]).decode("ascii")
    spaced = " ".join(hx[i:i+2] for i in range(0, len(hx), 2))
    return spaced + ("\n… [hex preview truncated]" if len(b) > 256 else "")

def main():
    ap = argparse.ArgumentParser(
        description="Dump macOS clipboard (pasteboard) flavors, including binary payloads."
    )
    ap.add_argument("--all", action="store_true",
                    help="Show previews for all types (not just a smart subset).")
    ap.add_argument("--prefer", help="Prefer this UTI when printing one full payload preview.")
    ap.add_argument("--dump-dir", type=Path,
                    help="Directory to write raw payload files (one per type).")
    ap.add_argument("--quiet", action="store_true",
                    help="Only print a summary table (still dumps files if --dump-dir).")
    args = ap.parse_args()

    pb = NSPasteboard.generalPasteboard()
    items = pb.pasteboardItems()
    if not items:
        print("Pasteboard is empty.")
        return

    # We’ll inspect the first item by default (most copy actions put a single item)
    # but still enumerate all for completeness.
    total_items = len(items)
    print(f"Found {total_items} pasteboard item(s).")

    for idx, item in enumerate(items, 1):
        print(f"\n=== Item #{idx} ===")
        types = list(item.types())
        if not types:
            print("  (no types)")
            continue

        # Summary
        for t in types:
            data = item.dataForType_(t)
            size = int(data.length()) if data else 0
            print(f"  • {t}  ({size} bytes)")

        # Dump raw files if requested
        if args.dump_dir:
            args.dump_dir.mkdir(parents=True, exist_ok=True)
            for t in types:
                data = item.dataForType_(t)
                if not data:
                    continue
                b = bytes(data)
                ext = guess_ext(t)
                safe = sanitize(t)
                out = args.dump_dir / f"item{idx}_{safe}.{ext}"
                out.write_bytes(b)
                print(f"  → wrote {out} ({len(b)} bytes)")

        if args.quiet:
            continue

        # Decide which types to show
        show_types = types if args.all else (
            # smart subset: text-ish types first, then everything else if nothing matches
            [t for t in types if t in TEXTY_UTIS] or types
        )

        # If a preferred UTI is supplied and present, show that first/only
        if args.prefer and args.prefer in types:
            show_types = [args.prefer]

        # Pretty-print previews
        for t in show_types:
            data = item.dataForType_(t)
            if not data:
                continue
            b = bytes(data)
            print(f"\n--- Preview: {t} ({len(b)} bytes) ---")
            print(bytes_preview(b))

            # Helpful decode of RTF control words and HTML tags in preview
            if t == "public.rtf" and b.startswith(b"{\\rtf"):
                print("\n[Note] This is RTF; look for control words like \\field, \\fldinst, \\objdata which can hide IDs.")
            if t == "public.html":
                print("\n[Note] This is HTML; search for data-* attributes or hidden spans containing IDs.")

        # If user asked to prefer one type, don’t spam multiple previews
        if args.prefer:
            continue

    # Extra hint for Teams custom types
    print("\nTip: If you see a custom UTI (e.g., something like com.microsoft.teams.*), "
          "use --prefer with that exact UTI (and/or --dump-dir) to grab the raw payload.")

if __name__ == "__main__":
    sys.exit(main())