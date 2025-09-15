# tarr/graph_watch.py
import os
import time
import json
import requests
import datetime as dt
from typing import Optional, Tuple, List, Dict

import msal

GRAPH = "https://graph.microsoft.com/v1.0"

def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def _to_aware_utc(ts: Optional[str]) -> Optional[dt.datetime]:
    """
    Parse Graph timestamps like '2025-09-16T07:23:45.123Z' into aware UTC datetimes.
    Returns None if ts is falsy or unparsable.
    """
    if not ts:
        return None
    try:
        # fromisoformat understands offsets; replace 'Z' with '+00:00'
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None

class GraphWatcher:
    """
    Minimal Microsoft Graph helper for Teams channel message/reply workflows.
    Device-code auth (Public Client), cached to a file.
    """

    def __init__(self, tenant_id: str, client_id: str, scopes: List[str], cache_path: str = "auth/msal_token.json"):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.scopes = scopes
        self.cache_path = cache_path

        self._cache = msal.SerializableTokenCache()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    self._cache.deserialize(f.read())
            except Exception:
                pass

        self._app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=self._cache,
        )
        self._token: Optional[str] = None

    # ---------- Auth & HTTP ----------

    def _save_cache(self):
        if self._cache.has_state_changed:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w") as f:
                f.write(self._cache.serialize())

    def acquire_token(self) -> str:
        result = self._app.acquire_token_silent(self.scopes, account=None)
        if not result:
            flow = self._app.initiate_device_flow(scopes=self.scopes)
            if "user_code" not in flow:
                raise RuntimeError("Failed to create device flow")
            print(f"[GRAPH] Visit {flow['verification_uri']} and enter code: {flow['user_code']}")
            result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"MSAL auth failed: {result.get('error_description')}")
        self._save_cache()
        self._token = result["access_token"]
        return self._token

    def _get_raw(self, url: str, params: Dict = None) -> requests.Response:
        if not self._token:
            self.acquire_token()
        r = requests.get(url, headers={"Authorization": f"Bearer {self._token}"}, params=params or {})
        if r.status_code == 401:
            # Token may have expired; refresh once
            self.acquire_token()
            r = requests.get(url, headers={"Authorization": f"Bearer {self._token}"}, params=params or {})
        return r

    def _get(self, url: str, params: Dict = None) -> Dict:
        r = self._get_raw(url, params)
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = {"text": r.text}
            raise RuntimeError(f"Graph GET {url} failed {r.status_code}: {json.dumps(detail)[:800]}")
        return r.json()

    # ---------- Paging (no $top) ----------

    def _paged(self, url: str, params: Dict = None, limit: int = 1000):
        """
        Yield items across @odata.nextLink pages.
        DOES NOT SEND $top to avoid 'Top is not allowed' on Teams message endpoints.
        """
        p = dict(params or {})
        p.pop("$top", None)  # just in case

        while True:
            data = self._get(url, p if p else None)
            for it in data.get("value", []):
                yield it
                limit -= 1
                if limit <= 0:
                    return
            next_link = data.get("@odata.nextLink")
            if not next_link:
                return
            url, p = next_link, None  # follow absolute nextLink; Graph ignores params after this

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
        max_checks:int = 3,
    ) -> Optional[str]:
        """
        Scan recent channel messages (roots only) for one authored by you that
        contains the text_hint (prefix match forgiving) and is newer than since_utc.
        """
        text_hint = (text_hint or "").strip()

        # Ensure since_utc is aware (UTC)
        if since_utc.tzinfo is None:
            since_utc = since_utc.replace(tzinfo=dt.timezone.utc)

        for _ in range(max_checks):
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
                return candidates[0][1]
            time.sleep(0.8)
        return None

    def wait_for_reply(
        self,
        team_id: str,
        channel_id: str,
        root_id: str,
        bot_display_name: str,
        timeout_s:int = 90,
        poll_every_s:float = 1.5,
    ) -> Tuple[Optional[Dict], List[Dict]]:
        """
        Poll replies under a root message until a reply authored by the bot
        is found or until timeout. Returns (reply_dict_or_None, all_replies_list).
        Each reply item has: id, author, text(html), createdDateTime.
        """
        deadline = time.time() + max(1, timeout_s)
        seen: set = set()
        all_replies: List[Dict] = []
        bot_match = (bot_display_name or "").strip().lower()

        while time.time() < deadline:
            page_items: List[Dict] = []
            for r in self._paged(
                f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies",
                params=None,
                limit=500,
            ):
                page_items.append(r)

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
                    return item, all_replies

            time.sleep(poll_every_s)

        return None, all_replies

# Convenience: if you need a since_utc for "last message I just sent"
def since_utc_seconds_ago(seconds: int = 30) -> dt.datetime:
    return _utc_now() - dt.timedelta(seconds=max(0, seconds))