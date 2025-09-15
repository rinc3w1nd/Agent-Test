import time, datetime as dt, os, json
from typing import Optional, Tuple, List, Dict
import requests
import msal

GRAPH = "https://graph.microsoft.com/v1.0"
PAGE_SIZE_DEFAULT = 50  # safer than 200; many endpoints cap or complain

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
            # Token might have expired; refresh once
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
            raise RuntimeError(f"Graph GET {url} failed {r.status_code}: {json.dumps(detail)[:600]}")
        return r.json()

    def _paged(self, url: str, params: Dict = None, limit: int = 1000):
        """Yield items across @odata.nextLink pages."""
        p = dict(params or {})
        if "$top" not in p:
            p["$top"] = str(PAGE_SIZE_DEFAULT)
        while True:
            data = self._get(url, p)
            for it in data.get("value", []):
                yield it
                limit -= 1
                if limit <= 0:
                    return
            next_link = data.get("@odata.nextLink")
            if not next_link:
                return
            # when nextLink present, Graph ignores params; follow the absolute URL
            url, p = next_link, None

    # ---------- Resolution helpers ----------

    def resolve_team_id(self, team_display_name: str) -> Optional[str]:
        """Return the team id by displayName (case-insensitive)."""
        name = team_display_name.strip().lower()
        for t in self._paged(f"{GRAPH}/me/joinedTeams",
                             params={"$select":"id,displayName","$top": str(PAGE_SIZE_DEFAULT)},
                             limit=2000):
            if (t.get("displayName","") or "").strip().lower() == name:
                return t.get("id")
        return None

    def resolve_channel_id(self, team_id: str, channel_display_name: str) -> Optional[str]:
        """Return the channel id within a team by displayName (case-insensitive)."""
        name = channel_display_name.strip().lower()
        for c in self._paged(f"{GRAPH}/teams/{team_id}/channels",
                             params={"$select":"id,displayName","$top": str(PAGE_SIZE_DEFAULT)},
                             limit=2000):
            if (c.get("displayName","") or "").strip().lower() == name:
                return c.get("id")
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
            # Page through recent messages (Graph may cap $top)
            candidates = []
            for m in self._paged(
                f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages",
                params={"$top": str(PAGE_SIZE_DEFAULT)},
                limit=250
            ):
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
                candidates.append((created_dt or dt.datetime.min, m.get("id")))
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
        bot_match = (bot_display_name or "").strip().lower()

        while time.time() < deadline:
            # fetch a page of replies; Graph may paginate beyond $top
            page_items: List[Dict] = []
            try:
                for r in self._paged(
                    f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies",
                    params={"$top": str(PAGE_SIZE_DEFAULT)},
                    limit=500
                ):
                    page_items.append(r)
            except RuntimeError as e:
                # Surface the exact 400 body once and break
                raise

            for r in page_items:
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