from __future__ import annotations

import os
import time
import warnings
from dataclasses import dataclass
from typing import Optional, Dict, Union

import requests
import certifi
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# Suppress SSL warnings when verification is disabled
warnings.filterwarnings('ignore', message='Unverified HTTPS request')


class MoltbookError(RuntimeError):
    pass


@dataclass
class MoltbookClient:
    base_url: str
    api_key: Optional[str] = None
    timeout_s: int = 30
    verify_ssl: bool = True

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, json: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"

        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                # Create a requests Session for better control
                session = requests.Session()
                
                # If SSL verification is disabled, configure appropriately
                if not self.verify_ssl:
                    # Disable SSL warnings
                    try:
                        from requests.packages.urllib3.exceptions import InsecureRequestWarning
                        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
                    except:
                        pass
                
                verify = certifi.where() if self.verify_ssl else False
                r = session.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    json=json,
                    timeout=self.timeout_s,
                    verify=verify,
                )
                session.close()

                # Retryable conditions
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * attempt)
                    continue

                if not r.ok:
                    raise MoltbookError(f"{r.status_code} {r.text}")

                data = r.json()
                # Handle wrappers like {"success": true, "data": {...}}
                if isinstance(data, dict) and isinstance(data.get("data"), dict):
                    return data["data"]
                return data

            except (requests.RequestException, ValueError) as e:
                last_exc = e
                time.sleep(1.0 * attempt)

        raise MoltbookError(f"Request failed after retries: {last_exc}")

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
        # Note: This assumes query params, but _request doesn't handle them yet
        # For now, we'll append to path
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"/posts?{query}" if query else "/posts"
        return self._request("GET", path)

    def get_agents(self, limit: int = 20) -> dict:
        """Get list of agents to discover"""
        return self._request("GET", f"/agents?limit={limit}")

    def get_agent_profile(self, agent_id: str) -> dict:
        """Get profile of a specific agent"""
        return self._request("GET", f"/agents/{agent_id}")

    def follow_agent(self, agent_id: str) -> dict:
        """Follow an agent"""
        return self._request("POST", f"/agents/{agent_id}/follow")

    def get_feed(self, filter_type: Optional[str] = None, limit: int = 20) -> dict:
        """Get the general feed or filtered feed"""
        path = "/feed"
        if filter_type:
            path += f"?filter={filter_type}&limit={limit}"
        else:
            path += f"?limit={limit}"
        return self._request("GET", path)

    def search_posts(self, query: str, limit: int = 20) -> dict:
        """Search posts semantically"""
        return self._request("GET", f"/search?q={query}&limit={limit}")

    def get_submolts(self) -> dict:
        """Get list of submolts"""
        return self._request("GET", "/submolts")

    def get_post(self, post_id: str) -> dict:
        """Get details of a specific post"""
        return self._request("GET", f"/posts/{post_id}")
