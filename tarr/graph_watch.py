import time, datetime as dt, os
from typing import Optional, Tuple, List, Dict
import requests
import msal

GRAPH = "https://graph.microsoft.com/v1.0"

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
                self._cache.deserialize(open(cache_path, "r").read())
            except Exception:
                pass
        self._app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=self._cache
        )
        self._token = None

    def _save_cache(self):
        if self._cache.has_state_changed:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w") as f:
                f.write(self._cache.serialize())

    def acquire_token(self) -> str:
        """
        Get an access token via cached or device-code flow.
        """
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

    def _get(self, url: str, params: Dict = None) -> Dict:
        if not self._token:
            self.acquire_token()
        r = requests.get(url, headers={"Authorization": f"Bearer {self._token}"}, params=params or {})
        if r.status_code == 401:
            # Token might have expired; refresh once
            self.acquire_token()
            r = requests.get(url, headers={"Authorization": f"Bearer {self._token}"}, params=params or {})
        r.raise_for_status()
        return r.json()

    # ---------- Resolution helpers ----------

    def resolve_team_id(self, team_display_name: str) -> Optional[str]:
        """
        Return the team id whose displayName matches (case-insensitive).
        """
        data = self._get(f"{GRAPH}/me/joinedTeams", params={"$select":"id,displayName","$top":"200"})
        for t in data.get("value", []):
            if t.get("displayName","").strip().lower() == team_display_name.strip().lower():
                return t["id"]
        return None

    def resolve_channel_id(self, team_id: str, channel_display_name: str) -> Optional[str]:
        """
        Return the channel id within a team by displayName (case-insensitive).
        """
        data = self._get(f"{GRAPH}/teams/{team_id}/channels", params={"$select":"id,displayName","$top":"200"})
        for c in data.get("value", []):
            if c.get("displayName","").strip().lower() == channel_display_name.strip().lower():
                return c["id"]
        return None

    # ---------- Message search & reply polling ----------

    def find_recent_root_from_me(
        self,
        team_id: str,
        channel_id: str,
        since_utc: dt.datetime,
        text_hint: str,
        max_checks:int=3
    ) -> Optional[str]:
        """
        Scan recent channel messages (roots only) for one authored by you that
        contains the text_hint (prefix match forgiving) and is newer than since_utc.
        """
        text_hint = (text_hint or "").strip()
        for _ in range(max_checks):
            data = self._get(
                f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages",
                params={"$top":"50"}
            )
            candidates = []
            for m in data.get("value", []):
                if m.get("replyToId"):
                    continue  # only root messages
                created = m.get("createdDateTime")
                try:
                    created_dt = dt.datetime.fromisoformat(created.replace("Z","+00:00"))
                except Exception:
                    created_dt = None
                if created_dt and created_dt < since_utc:
                    continue
                body = (m.get("body",{}) or {}).get("content","") or ""
                if text_hint and text_hint[:60].lower() not in body.lower():
                    continue
                candidates.append((created_dt or dt.datetime.min, m["id"]))
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
        timeout_s:int=90,
        poll_every_s:float=1.5
    ):
        """
        Poll replies under a root message until a reply authored by the bot
        is found or until timeout. Returns (reply_dict_or_None, all_replies_list).
        Each reply item has: id, author, text(html), createdDateTime.
        """
        deadline = time.time() + max(1, timeout_s)
        seen = set()
        all_replies = []
        bot_match = bot_display_name.strip().lower()
        while time.time() < deadline:
            data = self._get(
                f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies",
                params={"$top":"50"}
            )
            vals = data.get("value", [])
            for r in vals:
                rid = r.get("id")
                if rid in seen:
                    continue
                seen.add(rid)
                author = ""
                if r.get("from") and r["from"].get("user"):
                    author = r["from"]["user"].get("displayName","")
                text = (r.get("body",{}) or {}).get("content","") or ""
                html = text
                item = {
                    "id": rid,
                    "author": author,
                    "text": text,
                    "html": html,
                    "createdDateTime": r.get("createdDateTime")
                }
                all_replies.append(item)
                if author.strip().lower() == bot_match:
                    return item, all_replies
            time.sleep(poll_every_s)
        return None, all_replies