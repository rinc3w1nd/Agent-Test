import argparse, asyncio
from .runner import main_entry

def parse():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="examples/teams_recon.yaml", help="Path to YAML config")
    ap.add_argument("--init", action="store_true", help="Initialize auth state and exit")
    ap.add_argument("--show-controls", action="store_true", help="Launch Tk panel")
    ap.add_argument("--controls-on-enter", action="store_true", help="(Kept for overlay; Tk ignores)")
    ap.add_argument("--dry-run", action="store_true", help="No-ops for sending/recording")
    return ap.parse_args()

def main():
    args = parse()
    asyncio.run(main_entry(args.config, args.init, args.show_controls, args.controls_on_enter, args.dry_run))

if __name__ == "__main__":
    main()