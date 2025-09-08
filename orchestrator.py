#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchestrator for multi-turn escalation corpora.

- Accepts one or more input JSONL corpora.
- Groups cases by family (e.g., escal-001a/b/c → family 'escal-001').
- Orders stages per family (default: a,b,c) and concatenates into one flat JSONL.
- Optionally invokes runner.py on the generated file.

IDs supported as staged families:
  - <base><stage>        e.g., escal-001a
  - <base>-<stage>       e.g., escal-001-a
  - Optional explicit "stage" field in the JSON line (overrides parsing)

Non-staged items (no a/b/c) are treated as singletons and kept in input order.

Usage:
  python3 orchestrator.py --inputs corpora/*.jsonl --out runs/merged_escalation.jsonl
  # or immediately run:
  python3 orchestrator.py --inputs corpora/*.jsonl --out runs/merged.jsonl \
      --run-runner ./runner.py --config config.yaml
"""

import argparse, json, os, re, sys, glob, subprocess
from collections import defaultdict

STAGE_ORDER_DEFAULT = ["a","b","c","d","e"]

ID_STAGE_PATTERNS = [
    re.compile(r"^(?P<base>.+?)-(?P<stage>[a-zA-Z])$"),   # foo-001-a
    re.compile(r"^(?P<base>.+?)(?P<stage>[a-zA-Z])$"),    # foo-001a
]

def parse_line(line: str):
    line = line.strip()
    if not line:
        return None
    obj = json.loads(line)
    if not isinstance(obj, dict):
        return None
    return obj

def parse_id_family(id_str: str, explicit_stage: str | None = None):
    """
    Return (family, stage, is_staged)
    """
    if not id_str:
        return ("", "", False)
    if explicit_stage:
        return (id_str, explicit_stage.lower(), True)
    for rx in ID_STAGE_PATTERNS:
        m = rx.match(id_str)
        if m:
            base = m.group("base")
            stage = m.group("stage").lower()
            # Heuristic: ensure the "stage" looks like a single letter a..z
            if len(stage) == 1 and stage.isalpha():
                return (base, stage, True)
    # Not staged
    return (id_str, "", False)

def load_corpora(paths):
    files = []
    for g in paths:
        files.extend(glob.glob(g))
    files = sorted(set(files))
    if not files:
        print("No input files found.", file=sys.stderr)
        sys.exit(2)

    items = []
    for p in files:
        with open(p, "r", encoding="utf-8") as f:
            for ln, raw in enumerate(f, 1):
                if not raw.strip():
                    continue
                try:
                    obj = parse_line(raw)
                    if obj is None:
                        continue
                except Exception as e:
                    print(f"[!] JSON parse error {p}:{ln}: {e}", file=sys.stderr)
                    continue
                obj["_source_file"] = os.path.basename(p)
                obj["_source_line"] = ln
                items.append(obj)
    return items

def build_sequences(items, stage_order):
    families = defaultdict(list)   # family -> list of (stage_key, obj)
    singletons = []                # keep original order for singletons

    for obj in items:
        _id = obj.get("id","")
        stage_field = obj.get("stage")
        fam, stage, is_staged = parse_id_family(_id, stage_field)
        if is_staged:
            families[fam].append((stage, obj))
        else:
            singletons.append(obj)

    # Order staged families per stage_order; unknown stages go last by name
    stage_index = {s:i for i,s in enumerate(stage_order)}
    ordered = []

    # Keep families sorted by natural key (appearance order of first member)
    # We’ll track first seen index:
    first_seen = {}
    counter = 0
    for obj in items:
        _id = obj.get("id","")
        fam, stage, is_staged = parse_id_family(_id, obj.get("stage"))
        if is_staged and fam not in first_seen:
            first_seen[fam] = counter
            counter += 1

    for fam in sorted(families.keys(), key=lambda k: first_seen.get(k, 1_000_000)):
        seq = families[fam]
        seq.sort(key=lambda t: stage_index.get(t[0], 999) if len(t[0])==1 else 999)
        ordered.extend([o for _, o in seq])

    # Preserve singleton order as they appeared across inputs, but AFTER staged families,
    # or interleave? Most users want staged chains first. If you prefer interleave,
    # merge by original order instead.
    merged = ordered + singletons
    return merged

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="Input JSONL globs")
    ap.add_argument("--out", required=True, help="Output JSONL path")
    ap.add_argument("--stage-order", default="a,b,c", help="Comma list of stage suffixes (default: a,b,c)")
    ap.add_argument("--run-runner", help="Path to runner.py to invoke after writing")
    ap.add_argument("--config", help="Path to runner config.yaml")
    ap.add_argument("--headless", action="store_true", help="If you pass through to runner, set headful=false via config (optional manual)")
    args = ap.parse_args()

    stage_order = [s.strip().lower() for s in args.stage_order.split(",") if s.strip()]

    items = load_corpora(args.inputs)
    merged = build_sequences(items, stage_order)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for obj in merged:
            # ensure @BOT stays as-is (runner replaces it), and keep original fields
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"[+] Wrote merged corpus: {args.out}")
    print(f"    Families staged with order: {stage_order}")
    staged_count = sum(1 for o in merged if parse_id_family(o.get('id',''), o.get('stage'))[2])
    print(f"    Total lines: {len(merged)} (staged: {staged_count} | singletons: {len(merged)-staged_count})")

    if args.run_runner:
        cmd = [sys.executable, args.run_runner, "--corpus", args.out]
        if args.config:
            cmd += ["--config", args.config]
        print("[*] Launching runner:", " ".join(cmd))
        # Note: your SSO may require the --pause-after-login flag in runner; add it here if you use it.
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[!] runner.py failed with exit code {e.returncode}", file=sys.stderr)
            sys.exit(e.returncode)

if __name__ == "__main__":
    main()