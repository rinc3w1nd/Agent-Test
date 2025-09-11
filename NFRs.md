Here’s the NFR → status map for the current skeleton.

1) Reliability & Determinism
	•	R1. Non-persistent + state file; —init flow; fail if state missing — DONE

2) Observability (Logs & Audit)
	•	O1. Startup banner (mode/channel/state/url) — DONE
	•	O2. Action breadcrumbs to per-run audit file audit/<script>-yyMMdd_HHmm.txt — DONE

3) Data & Artifacts
	•	D1. Append-only text/html with run headers — DONE
	•	D2. Canonical timestamps (run + action-time screenshot names) — DONE
	•	D3. Atomic writes (tmp→rename) — DONE
	•	D4. Auto-create directories — DONE
	•	D5. Echo full corpus fields in text — DONE
	•	D6. Store plain-text extraction + raw innerHTML — DONE
	•	D7. Operator note persisted in text — DONE
	•	D8. reply_detected + reply_len_chars flags — DONE

4) Performance
	•	P1. Doubled readiness waits (configurable) — DONE
	•	P2. @mention 5/5/5s attempt windows — DONE

5) Compatibility & Portability
	•	C1. Edge channel configurable (msedge) — DONE
	•	C2. macOS primary support — DONE
	•	C3. Playwright min-version check (≥1.45) — TODO (add runtime version assert/warn)

6) Security & Privacy
	•	S1. Opt-in load/save state via YAML — DONE
	•	S2. POSIX 0600 on auth_state.json — DONE
	•	S3. No secret logging (paths only) — DONE

7) Usability (Operator Experience)
	•	U1. Overlay buttons (Load/Send @BOT/Send/Prev/Next/Record Status) — DONE
	•	U2. Auto-send checkbox (session-scoped) — DONE
	•	U3. Operator-driven capture; prefill note with detected reply — DONE

8) Configurability
	•	F1. YAML + CLI overrides (—config, —init, —show-controls, —controls-on-enter, —dry-run) — DONE
	•	F2. Timing knobs (delays/timeouts/retries) — DONE
	•	F3. File paths (artifacts/audit/state) — DONE

9) Maintainability
	•	M1. Single launcher path (non-persistent + storage_state) — DONE
	•	M2. Small, pure helpers (launcher/audit/artifacts/mention/capture/overlay) — DONE
	•	M3. Lint/type gates (ruff/flake8/mypy) — TODO (add config/CI)

10) Testability
	•	T1. —init flow saves state — DONE
	•	T2. —dry-run logs without typing/clicking — DONE
	•	T3. Golden corpus + expected artifacts for regression — PARTIAL (example corpus included; no golden assertions)

11) Failure Modes & Recovery
	•	FMR1. Missing/invalid state → explicit error; exit — DONE
	•	FMR2. Mention bind fail → audit BIND_FAIL, continue — DONE
	•	FMR3. Reply capture only on operator “Record Status” (no timers) — DONE

12) Compliance & Records
	•	CR1. Per-run audit file with millisecond timestamps — DONE
	•	CR2. Run manifest artifacts/run.<ts>.json (config hash, URL, channel) — DONE
	•	CR3. Filenames sanitized to [A-Za-z0-9._-] — DONE

Open items to finish:
	•	C3: Add a Playwright version check at startup.
	•	M3: Add lint/type tooling (ruff/flake8/mypy) + simple CI.
	•	T3: Add a tiny validator that diff-checks artifacts vs “golden” outputs.