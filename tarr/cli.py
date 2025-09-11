import asyncio, argparse
from .runner import main_entry

def main():
    ap = argparse.ArgumentParser(description="Teams Agent Recon Runner (TARR)")
    ap.add_argument("--config", default="examples/teams_recon.yaml", help="Path to YAML config")
    ap.add_argument("--init", action="store_true", help="Initialize auth state (login then save to storage_state_path)")
    ap.add_argument("--show-controls", action="store_true", help="Inject live control overlay")
    ap.add_argument("--controls-on-enter", action="store_true", help="Wait for Enter before injecting overlay")
    ap.add_argument("--dry-run", action="store_true", help="Log actions only; do not type/click or write artifacts")
    args = ap.parse_args()
    asyncio.run(main_entry(args.config, args.init, args.show_controls, args.controls_on_enter, args.dry_run))

if __name__ == "__main__":
    main()