# TARR — Teams Agent Recon Runner (clean skeleton)

Implements the canonical NFRs: non-persistent launch + storage state, overlay controls,
operator-triggered capture, append-only artifacts, per-run audit file.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install playwright browsers (Edge channel)
playwright install msedge