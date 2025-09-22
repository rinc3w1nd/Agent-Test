python - <<'PY'
import csv, json,sys
infile="gh_skillchain_tests_120_fluff_with_prompts.csv"
outfile="web_tests_gists_payloads_from_csv.jsonl"
with open(infile, newline='', encoding='utf-8') as inf, open(outfile,'w',encoding='utf-8') as outf:
    reader=csv.DictReader(inf)
    for r in reader:
        # keep only fields the gist script needs:
        obj={
            "test_id": r.get("test_id") or r.get("Test ID") or r.get("id"),
            "content_block": r.get("content_block") or r.get("content") or r.get("content_block"),
            "canary_token": r.get("canary_token") or r.get("canary")
        }
        if not obj["test_id"] or not obj["content_block"]:
            # skip incomplete rows
            continue
        outf.write(json.dumps(obj, ensure_ascii=False) + "\\n")
print("Wrote", outfile)
PY