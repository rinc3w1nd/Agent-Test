# tarr/graph_watch.py
import os, time, json, requests
import datetime as dt
from typing import Optional, Tuple, List, Dict

import msal

GRAPH = "https://graph.microsoft.com/v1.0"

def _dbg(msg: str):
    if os.environ.get("TARR_VERBOSE", "1") != "0":
        print(f"[DBG][GRAPH] {msg}", flush=True)

def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def _to_aware_utc(ts: Optional[str]) -> Optional[dt.datetime]:
    """Parse Graph '...Z' timestamp to aware UTC datetime."""
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None

class GraphWatcher:
    """
    Minimal Microsoft Graph helper for Teams channel message/reply workflows.
    Uses device-code auth (Public Client), with a serialized token cache.
    All networking timeouts/retries are configurable via YAML `cfg`.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        scopes: List[str],
        cache_path: str = "auth/msal_token.json",
        cfg: Dict = None,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.scopes = scopes or []
        self.cache_path = cache_path
        self.cfg = cfg or {}

        # --- Configurable networking knobs (with defaults) ---
        self.connect_timeout = float(self.cfg.get("graph_connect_timeout_s", 3.1))
        self.read_timeout    = float(self.cfg.get("graph_read_timeout_s", 15.0))
        self.max_retries     = int(self.cfg.get("graph_max_retries", 3))
        self.retry_backoff_base = float(self.cfg.get("graph_retry_backoff_base_s", 1.5))
        self.retry_backoff_max  = float(self.cfg.get("graph_retry_backoff_max_s", 8.0))

        # --- Token cache (MSAL) ---
        self._cache = msal.SerializableTokenCache()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    self._cache.deserialize(f.read())
                _dbg(f"Loaded MSAL cache from {cache_path}")
            except Exception as e:
                _dbg(f"Ignoring corrupt MSAL cache: {e!r}")

        self._app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=self._cache,
        )
        self._token: Optional[str] = None

    # ---------- Auth ----------
    def _save_cache(self):
        try:
            if self._cache.has_state_changed:
                d = os.path.dirname(self.cache_path) or "."
                os.makedirs(d, exist_ok=True)
                with open(self.cache_path, "w") as f:
                    f.write(self._cache.serialize())
                try:
                    os.chmod(self.cache_path, 0o600)
                except Exception:
                    pass
                _dbg(f"Token cache saved to {self.cache_path}")
        except Exception as e:
            print(f"[GRAPH] Failed to save token cache: {e!r}", flush=True)

    def acquire_token(self) -> str:
        result = self._app.acquire_token_silent(self.scopes, account=None)
        if result and "access_token" in result:
            self._token = result["access_token"]
            _dbg(f"Loaded token silently from cache {self.cache_path}")
            return self._token

        flow = self._app.initiate_device_flow(scopes=self.scopes)
        if "user_code" not in flow:
            raise RuntimeError("Failed to create device flow")
        # Print device-code UX once
        print(f"[GRAPH] Visit {flow['verification_uri']} and enter code: {flow['user_code']}", flush=True)
        result = self._app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise RuntimeError(f"MSAL auth failed: {result.get('error_description')}")

        self._token = result["access_token"]
        self._save_cache()
        return self._token

    # ---------- Requests ----------
    def _req(self, url: str, params: Dict = None) -> requests.Response:
        """
        GET with timeouts + limited retries:
          - 401: refresh token once
          - 429/5xx: obey Retry-After (if present) or exponential backoff
        """
        if not self._token:
            self.acquire_token()

        params = params or {}
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                _dbg(f"GET {url} attempt {attempt}")
                r = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    params=params,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
            except requests.exceptions.RequestException as e:
                last_exc = e
                _dbg(f"Network error {e!r}, retrying…")
                time.sleep(min(self.retry_backoff_base * attempt, self.retry_backoff_max))
                continue

            if r.status_code == 401 and attempt == 1:
                _dbg("401 Unauthorized, refreshing token")
                self.acquire_token()
                continue

            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                ra = r.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra and str(ra).isdigit() else self.retry_backoff_base * attempt
                except Exception:
                    delay = self.retry_backoff_base * attempt
                _dbg(f"{r.status_code} backoff {delay}s")
                time.sleep(min(delay, self.retry_backoff_max))
                continue

            return r

        if last_exc:
            raise RuntimeError(f"Graph GET {url} failed (network): {last_exc!r}")
        return r  # return last response (likely error); caller will handle

    def _get(self, url: str, params: Dict = None) -> Dict:
        r = self._req(url, params)
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = {"text": r.text}
            raise RuntimeError(f"Graph GET {url} failed {r.status_code}: {json.dumps(detail)[:800]}")
        data = r.json()
        _dbg(f"Graph returned {len(data.get('value', []))} items; keys={list(data.keys())}")
        return data

    # ---------- Paging (no $top) ----------
    def _paged(self, url: str, params: Dict = None, limit: int = 1000):
        """
        Yield items across @odata.nextLink pages.
        DOES NOT SEND $top to avoid 'Top is not allowed' on Teams message endpoints.
        """
        p = dict(params or {})
        p.pop("$top", None)
        while True:
            data = self._get(url, p if p else None)
            items = data.get("value", []) or []
            for it in items:
                yield it
                limit -= 1
                if limit <= 0:
                    return
            next_link = data.get("@odata.nextLink")
            if not next_link:
                return
            _dbg(f"Following @odata.nextLink")
            url, p = next_link, None  # follow absolute link; Graph ignores params afterward

    # ---------- Resolution helpers ----------
    def resolve_team_id(self, team_display_name: str) -> Optional[str]:
        """Return the team id by displayName (case-insensitive)."""
        name = (team_display_name or "").strip().lower()
        for t in self._paged(f"{GRAPH}/me/joinedTeams", params=None, limit=2000):
            if (t.get("displayName", "") or "").strip().lower() == name:
                return t.get("id")
        return None

    def resolve_channel_id(self, team_id: str, channel_display_name: str) -> Optional[str]:
        """Return the channel id within a team by displayName (case-insensitive)."""
        name = (channel_display_name or "").strip().lower()
        for c in self._paged(f"{GRAPH}/teams/{team_id}/channels", params=None, limit=2000):
            if (c.get("displayName", "") or "").strip().lower() == name:
                return c.get("id")
        return None

    # ---------- Message search & reply polling ----------
    def find_recent_root_from_me(
        self,
        team_id: str,
        channel_id: str,
        since_utc: dt.datetime,
        text_hint: str,
        max_checks: int = 3,
    ) -> Optional[str]:
        """
        Scan recent channel messages (roots only) for one authored by you that
        contains the text_hint (prefix match forgiving) and is newer than since_utc.
        """
        text_hint = (text_hint or "").strip()

        # Ensure since_utc is aware (UTC)
        if since_utc.tzinfo is None:
            since_utc = since_utc.replace(tzinfo=dt.timezone.utc)

        for attempt in range(max_checks):
            _dbg(f"Scanning channel messages attempt {attempt+1}")
            candidates: List[Tuple[dt.datetime, str]] = []
            for m in self._paged(f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages", params=None, limit=250):
                if m.get("replyToId"):
                    continue  # only root messages
                created_dt = _to_aware_utc(m.get("createdDateTime"))
                if created_dt and created_dt < since_utc:
                    continue
                body = (m.get("body", {}) or {}).get("content", "") or ""
                if text_hint and text_hint[:60].lower() not in body.lower():
                    continue
                candidates.append((created_dt or dt.datetime.min.replace(tzinfo=dt.timezone.utc), m.get("id")))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                rid = candidates[0][1]
                _dbg(f"Matched root message id={rid}")
                return rid
            time.sleep(0.8)
        return None

    def wait_for_reply(
        self,
        team_id: str,
        channel_id: str,
        root_id: str,
        bot_display_name: str,
        timeout_s: int = 90,
        poll_every_s: float = 1.5,
    ) -> Tuple[Optional[Dict], List[Dict]]:
        """
        Poll replies under a root message until a reply authored by the bot
        is found or until timeout. Returns (reply_dict_or_None, all_replies_list).
        Each reply contains: id, author, text(html), createdDateTime.
        """
        deadline = time.time() + max(1, timeout_s)
        seen: set = set()
        all_replies: List[Dict] = []
        bot_match = (bot_display_name or "").strip().lower()
        poll_count = 0

        while time.time() < deadline:
            poll_count += 1
            _dbg(f"Poll #{poll_count}: fetching replies for root={root_id}")
            page_items: List[Dict] = []
            for r in self._paged(
                f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies",
                params=None,
                limit=500,
            ):
                page_items.append(r)
            _dbg(f"Poll #{poll_count}: got {len(page_items)} replies")

            for r in page_items:
                rid = r.get("id")
                if rid in seen:
                    continue
                seen.add(rid)
                author = ""
                if r.get("from") and r["from"].get("user"):
                    author = r["from"]["user"].get("displayName", "")
                text = (r.get("body", {}) or {}).get("content", "") or ""
                html = text
                item = {
                    "id": rid,
                    "author": author,
                    "text": text,
                    "html": html,
                    "createdDateTime": r.get("createdDateTime"),
                }
                all_replies.append(item)
                if author.strip().lower() == bot_match:
                    _dbg(f"Bot reply detected id={rid}")
                    return item, all_replies

            time.sleep(poll_every_s)

        _dbg("Poll timeout -- no bot reply found")
        return None, all_replies

# Convenience helper if you need a recent 'since' timestamp:
def since_utc_seconds_ago(seconds: int = 30) -> dt.datetime:
    return _utc_now() - dt.timedelta(seconds=max(0, seconds))