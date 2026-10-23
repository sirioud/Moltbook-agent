from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Dict

import requests
import urllib3

# Only these methods are safe to resend after a server error or timeout:
# a POST may already have been applied, and resending it creates duplicates.
IDEMPOTENT_METHODS = {"GET", "DELETE"}
RETRY_STATUSES = {500, 502, 503, 504}
MAX_RETRY_AFTER_S = 60


class MoltbookError(RuntimeError):
    pass


@dataclass
class MoltbookClient:
    base_url: str
    api_key: Optional[str] = None
    timeout_s: int = 30
    verify_ssl: bool = True
    session: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, json: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        retryable = method in IDEMPOTENT_METHODS

        last_error = "unknown error"
        for attempt in range(1, 4):
            try:
                r = self.session.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    json=json,
                    params=params,
                    timeout=self.timeout_s,
                    verify=self.verify_ssl,
                )
            except requests.RequestException as e:
                if not retryable:
                    raise MoltbookError(f"{method} {path} failed (not retried, may have been applied): {e}")
                last_error = str(e)
                time.sleep(1.0 * attempt)
                continue

            # 429 means the request was rejected, so it is safe to retry for any method,
            # but only if the server asks us to wait a short time.
            if r.status_code == 429:
                wait = _retry_after_seconds(r) or 1.5 * attempt
                if wait > MAX_RETRY_AFTER_S:
                    raise MoltbookError(f"429 rate limited (retry after: {r.headers.get('Retry-After', 'unknown')}s) {r.text}")
                last_error = f"429 {r.text}"
                time.sleep(wait)
                continue

            if retryable and r.status_code in RETRY_STATUSES:
                last_error = f"{r.status_code} {r.text}"
                time.sleep(1.5 * attempt)
                continue

            if not r.ok:
                raise MoltbookError(f"{r.status_code} {r.text}")

            try:
                data = r.json()
            except ValueError:
                raise MoltbookError(f"{r.status_code} non-JSON response: {r.text[:200]}")
            # Handle wrappers like {"success": true, "data": {...}}
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data

        raise MoltbookError(f"Request failed after retries: {last_error}")

    def register_agent(self, name: str, description: str) -> dict:
        return self._request("POST", "/agents/register", json={"name": name, "description": description})

    def me(self) -> dict:
        if not self.api_key:
            raise MoltbookError("Missing api_key. Put it in .env as MOLTBOOK_API_KEY.")
        return self._request("GET", "/agents/me")

    # Optional helpers (API paths can vary by version)
    def create_post(self, submolt: str, title: str, content: str) -> dict:
        return self._request("POST", "/posts", json={"submolt": submolt, "title": title, "content": content})

    def comment(self, post_id: str, content: str) -> dict:
        return self._request("POST", f"/posts/{post_id}/comments", json={"content": content})

    def upvote(self, post_id: str) -> dict:
        return self._request("POST", f"/posts/{post_id}/upvote")

    def get_home_feed(self) -> dict:
        """Get the home feed with posts from followed agents and recent activity"""
        return self._request("GET", "/home")

    def get_posts(self, submolt: Optional[str] = None, limit: int = 20) -> dict:
        """Get posts, optionally filtered by submolt"""
        params = {"limit": limit}
        if submolt:
            params["submolt"] = submolt
        return self._request("GET", "/posts", params=params)

    def get_agents(self, limit: int = 20) -> dict:
        """Get list of agents to discover"""
        return self._request("GET", "/agents", params={"limit": limit})

    def get_agent_profile(self, name: str) -> dict:
        """Get profile of a specific agent by name"""
        return self._request("GET", "/agents/profile", params={"name": name})

    def follow_agent(self, name: str) -> dict:
        """Follow an agent by name"""
        return self._request("POST", f"/agents/{name}/follow")

    def unfollow_agent(self, name: str) -> dict:
        """Unfollow an agent by name"""
        return self._request("DELETE", f"/agents/{name}/follow")

    def get_feed(self, filter_type: Optional[str] = None, limit: int = 20) -> dict:
        """Get the general feed or filtered feed"""
        params = {"limit": limit}
        if filter_type:
            params["filter"] = filter_type
        return self._request("GET", "/feed", params=params)

    def search_posts(self, query: str, limit: int = 20) -> dict:
        """Search posts semantically"""
        return self._request("GET", "/search", params={"q": query, "limit": limit})

    def get_submolts(self) -> dict:
        """Get list of submolts"""
        return self._request("GET", "/submolts")

    def get_post(self, post_id: str) -> dict:
        """Get details of a specific post"""
        return self._request("GET", f"/posts/{post_id}")


def _retry_after_seconds(r: requests.Response) -> Optional[float]:
    try:
        return float(r.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
