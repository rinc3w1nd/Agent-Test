#!/usr/bin/env python3
"""
iter_prompts.py

Usage:
  python iter_prompts.py <file.jsonl|file.csv>

Behaviour:
  - Loads rows from a JSONL or CSV file.
  - For each row prints:
      TEST: <test_id>
      >>> EXPLOIT PROMPT (press Enter to continue, 'q'+Enter to quit)
      (exploit prompt text)
    After Enter:
      >>> SAFE PROMPT (press Enter to continue to next test, 's'+Enter to skip safe prompt, 'q'+Enter to quit)
      (safe prompt text)
  - Expects the input to contain, per row:
      - test_id (optional; printed if present)
      - exploit_prompt (string)  OR exploit_prompt_template
      - safe_prompt   (string)  OR safe_prompt_template
    If prompts are missing it will try common fallbacks (exploit_prompt_template, safe_prompt_template).
"""
import sys, json, csv, os

def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln: 
                continue
            try:
                rows.append(json.loads(ln))
            except Exception as e:
                print(f"[WARN] skipping malformed jsonl line: {e}")
    return rows

def load_csv(path):
    rows = []
    with open(path, newline='', encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(dict(r))
    return rows

def get_field(d, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return ""

def interactive_iter(rows):
    n = len(rows)
    idx = 0
    while idx < n:
        r = rows[idx]
        test_id = get_field(r, "test_id", "testId", "id") or f"#{idx+1}"
        print("\n" + "="*80)
        print(f"TEST {idx+1}/{n}: {test_id}")
        print("-" * 80)
        exploit = get_field(r, "exploit_prompt", "exploit_prompt_template", "exploit_prompt_text")
        safe    = get_field(r, "safe_prompt", "safe_prompt_template", "safe_prompt_text")

        if not exploit and not safe:
            # fallback: maybe the file embeds 'content_block' and we have templates to produce prompts locally?
            content = get_field(r, "content_block", "content")
            if content:
                print("[INFO] No explicit prompts found but 'content_block' exists. Showing content (you can paste into prompts manually):\n")
                print(content)
                _cmd = input("\nPress Enter to continue to next test (q to quit): ").strip().lower()
                if _cmd == "q":
                    break
                idx += 1
                continue
            else:
                print("[WARN] No prompts or content found for this row. Skipping.")
                idx += 1
                continue

        # Show exploit prompt
        if exploit:
            print(">>> EXPLOIT PROMPT (press Enter to show SAFE prompt, 'q' then Enter to quit)")
            print("-" * 80)
            print(exploit)
        else:
            print("[INFO] No exploit prompt available for this test.")

        cmd = input("\n(press Enter to continue) ").strip().lower()
        if cmd == "q":
            print("Quitting.")
            break

        # Show safe prompt
        if safe:
            print("\n>>> SAFE PROMPT (press Enter to go to next test, 's' then Enter to skip safe prompt, 'q' then Enter to quit)")
            print("-" * 80)
            print(safe)
            cmd2 = input("\n(Enter to next, s to skip next, q to quit) ").strip().lower()
            if cmd2 == "q":
                print("Quitting.")
                break
            elif cmd2 == "s":
                idx += 1
                continue
            else:
                idx += 1
                continue
        else:
            print("\n[INFO] No safe prompt available for this test. Press Enter to continue.")
            cmd3 = input().strip().lower()
            if cmd3 == "q":
                print("Quitting.")
                break
            idx += 1

def main():
    if len(sys.argv) < 2:
        print("Usage: python iter_prompts.py <file.jsonl|file.csv>")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print("File not found:", path)
        sys.exit(1)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".jsonl" or ext == ".ndjson":
        rows = load_jsonl(path)
    elif ext == ".csv":
        rows = load_csv(path)
    else:
        # try jsonl first, then csv
        try:
            rows = load_jsonl(path)
            if not rows:
                rows = load_csv(path)
        except:
            rows = load_csv(path)
    if not rows:
        print("No rows loaded from", path)
        sys.exit(1)
    interactive_iter(rows)

if __name__ == "__main__":
    main()