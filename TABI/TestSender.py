# pip install pyyaml msal requests
import os, sys, json, html, errno
import yaml, requests, msal
from pathlib import Path
from contextlib import contextmanager

GRAPH = "https://graph.microsoft.com/v1.0"

def load_cfg(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def ensure_private_file(path: Path):
    """
    Best-effort: create parent, touch file, set restrictive perms on POSIX.
    On Windows, rely on user profile isolation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    try:
        if os.name == "posix":
            # -rw-------
            path.chmod(0o600)
    except Exception:
        pass

@contextmanager
def persistent_cache(cache_path: Path):
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try:
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
        except Exception:
            # Corrupt cache; start fresh
            cache = msal.SerializableTokenCache()
    yield cache
    if cache.has_state_changed:
        ensure_private_file(cache_path)
        cache_path.write_text(cache.serialize(), encoding="utf-8")

def acquire_token(cfg, cache):
    tenant = cfg["tenant_id"]
    client_id = cfg["client_id"]
    scopes = cfg.get("auth", {}).get("scopes", ["ChannelMessage.Send"])

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache
    )

    # Try silent first
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # Fall back to device code flow
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start device flow: {json.dumps(flow, indent=2)}")
    print(flow["message"])  # user completes this in a browser one time

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {json.dumps(result, indent=2)}")
    return result["access_token"]

def post_mention(token, team_id, channel_id, bot_app_id, bot_name, text):
    payload = {
        "body": {
            "contentType": "html",
            "content": f'<at id="0">{html.escape(bot_name)}</at> {html.escape(text)}'
        },
        "mentions": [{
            "id": 0,
            "mentionText": bot_name,
            "mentioned": {"application": {"id": bot_app_id}}
        }]
    }
    r = requests.post(
        f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=30
    )
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"Graph POST failed {r.status_code}: {detail}")
    return r.json()

def main():
    cfg = load_cfg()
    cache_path = Path(cfg.get("auth", {}).get("cache_path", ".msal_cache.bin")).expanduser()

    with persistent_cache(cache_path) as cache:
        token = acquire_token(cfg, cache)

    team_id = cfg["team"]["id"]
    channel_id = cfg["channel"]["id"]
    bot_app_id = cfg["bot"]["app_id"]
    bot_name = cfg["bot"]["name"]
    text = cfg["message"]["text"]

    result = post_mention(token, team_id, channel_id, bot_app_id, bot_name, text)
    msg_id = result.get("id", "<unknown>")
    print(f"Sent. messageId={msg_id}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)