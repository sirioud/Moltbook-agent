"""HTTP client for the Moltbook API (https://www.moltbook.com/api/v1).

Moltbook is a social network for AI agents. This client wraps the current API
surface (spec ``skill.md`` v1.12.0): agents, posts, comments, votes, submolts,
feeds, semantic search, notifications, labels/roles and the anti-spam
verification challenges.

Design notes
------------
* Query strings are built with ``requests`` ``params=`` rather than by hand.
* Responses are returned as the raw JSON dict (the API is not wrapped in a
  ``data`` envelope); callers pick the fields they need.
* Errors are mapped to typed exceptions. ``429`` raises
  :class:`MoltbookRateLimitError` carrying ``retry_after``; ``401``/``403``
  raise :class:`MoltbookAuthError`.
* Creating content may return a verification challenge. With
  ``auto_verify=True`` (the default) the client solves it and submits the
  answer; if it cannot, it raises :class:`MoltbookVerificationError` instead of
  leaving content silently unpublished.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from verification import (
    VerificationChallenge,
    VerificationError,
    extract_challenge,
    solve_verification,
    Solver,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "MoltbookAuthError",
    "MoltbookClient",
    "MoltbookError",
    "MoltbookRateLimitError",
    "MoltbookVerificationError",
]

DEFAULT_BASE_URL = "https://www.moltbook.com/api/v1"
DEFAULT_USER_AGENT = "moltbook-agent/2.0 (+https://github.com/sirioud/Moltbook-agent)"

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class MoltbookError(RuntimeError):
    """Base class for every Moltbook client error."""


class MoltbookAuthError(MoltbookError):
    """Authentication or authorisation failed (HTTP 401/403)."""


class MoltbookRateLimitError(MoltbookError):
    """Rate limited (HTTP 429). Carries ``retry_after`` seconds when known."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class MoltbookVerificationError(MoltbookError):
    """Content was created but its verification challenge could not be solved."""


def _retry_after(response: requests.Response) -> Optional[float]:
    """Extract a Retry-After delay from a 429 response, if the API supplied one."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except (TypeError, ValueError):
            pass
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        for key in ("retry_after_seconds", "retry_after"):
            value = body.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _error_message(response: requests.Response) -> str:
    """Best-effort human message from an error body (two known shapes)."""
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:300] if text else f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("error", "message", "hint"):
            value = body.get(key)
            if value:
                return str(value)
    return f"HTTP {response.status_code}"


@dataclass
class MoltbookClient:
    """Client for the Moltbook REST API.

    Args:
        base_url: API root. Defaults to the documented ``www`` host; the
            non-``www`` host is rewritten because it strips the auth header.
        api_key: Bearer token (``moltbook_...``). Required for authenticated
            calls; read endpoints such as ``/posts`` work without it.
        timeout_s: Per-request timeout in seconds.
        verify_ssl: Verify TLS certificates. Leave this on.
        max_retries: Attempts for retryable failures (5xx, 429, network).
        backoff_base: Base seconds for exponential-ish backoff between retries.
        auto_verify: Solve and submit verification challenges automatically.
        solver: Optional custom challenge solver (e.g. an LLM hook).
        session: Injected ``requests.Session``-like object (used by tests).
    """

    base_url: str = DEFAULT_BASE_URL
    api_key: Optional[str] = None
    timeout_s: int = 30
    verify_ssl: bool = True
    max_retries: int = 3
    backoff_base: float = 1.0
    auto_verify: bool = True
    solver: Optional[Solver] = None
    session: Any = None
    user_agent: str = DEFAULT_USER_AGENT
    _session_owned: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.base_url.startswith("https://moltbook.com"):
            # Without www the API redirects and drops the Authorization header.
            self.base_url = self.base_url.replace(
                "https://moltbook.com", "https://www.moltbook.com", 1
            )
        if self.session is None:
            self.session = requests.Session()
            self._session_owned = True
        if not self.verify_ssl:
            urllib3.disable_warnings(InsecureRequestWarning)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying session if this client created it."""
        if self._session_owned and self.session is not None:
            self.session.close()

    def __enter__(self) -> "MoltbookClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        require_auth: bool = True,
        retries: Optional[int] = None,
    ) -> dict[str, Any]:
        """Perform a request and return the parsed JSON body.

        The ``Authorization`` header is sent whenever an API key is configured.
        ``require_auth`` controls whether a *missing* key is an immediate error
        (authenticated endpoints) or acceptable (public read endpoints).

        Retries network errors, 5xx and 429 (honouring ``Retry-After``).
        """
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif require_auth:
            raise MoltbookAuthError(
                "Missing API key. Set MOLTBOOK_API_KEY (or pass api_key=...)."
            )

        attempts = self.max_retries if retries is None else retries
        attempts = max(1, attempts)
        verify = True if self.verify_ssl else False
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self.timeout_s,
                    verify=verify,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.backoff_base * attempt)
                    continue
                raise MoltbookError(f"Request to {url} failed: {exc}") from exc

            if response.status_code == 429:
                delay = _retry_after(response)
                if attempt < attempts:
                    time.sleep(delay if delay is not None else self.backoff_base * attempt)
                    continue
                raise MoltbookRateLimitError(
                    f"Rate limited: {_error_message(response)}", retry_after=delay
                )

            if response.status_code in (401, 403):
                raise MoltbookAuthError(
                    f"{response.status_code} {_error_message(response)}"
                )

            if response.status_code in _RETRYABLE_STATUS:
                if attempt < attempts:
                    time.sleep(self.backoff_base * attempt)
                    continue
                raise MoltbookError(
                    f"{response.status_code} {_error_message(response)}"
                )

            if not response.ok:
                raise MoltbookError(
                    f"{response.status_code} {_error_message(response)}"
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise MoltbookError(f"Invalid JSON from {url}: {exc}") from exc
            if not isinstance(body, dict):
                return {"data": body}
            return body

        raise MoltbookError(f"Request to {url} failed: {last_error}")

    def _paginate(
        self,
        path: str,
        params: dict[str, Any],
        item_key: str,
        max_items: int,
        require_auth: bool = True,
    ) -> list[dict[str, Any]]:
        """Follow ``next_cursor`` keyset pagination until ``max_items`` reached."""
        items: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            body = self._request(
                "GET", path, params=page_params, require_auth=require_auth
            )
            batch = body.get(item_key) or []
            if isinstance(batch, list):
                items.extend(item for item in batch if isinstance(item, dict))
            cursor = body.get("next_cursor")
            if not cursor or not body.get("has_more") or len(items) >= max_items:
                break
        return items[:max_items]

    # -- verification --------------------------------------------------------

    def verify(self, verification_code: str, answer: str) -> dict[str, Any]:
        """Submit an answer for a verification challenge."""
        return self._request(
            "POST",
            "/verify",
            json_body={"verification_code": verification_code, "answer": answer},
        )

    def solve_challenge(self, challenge: VerificationChallenge) -> str:
        """Solve ``challenge`` (custom solver if set, else deterministic)."""
        return solve_verification(challenge, solver=self.solver)

    def _maybe_verify(self, response: dict[str, Any]) -> dict[str, Any]:
        """Solve and submit a challenge embedded in a create response."""
        challenge = extract_challenge(response)
        if challenge is None or not self.auto_verify:
            return response
        try:
            answer = self.solve_challenge(challenge)
        except VerificationError as exc:
            raise MoltbookVerificationError(str(exc)) from exc
        result = self.verify(challenge.verification_code, answer)
        response["verification_result"] = result
        response["verified"] = bool(result.get("success"))
        return response

    # -- agents / auth -------------------------------------------------------

    def register_agent(self, name: str, description: str) -> dict[str, Any]:
        """Register a new agent. Returns ``api_key``/``claim_url`` (no auth needed)."""
        return self._request(
            "POST",
            "/agents/register",
            json_body={"name": name, "description": description},
            require_auth=False,
        )

    def me(self) -> dict[str, Any]:
        """Fetch the authenticated agent's profile."""
        return self._request("GET", "/agents/me")

    def status(self) -> dict[str, Any]:
        """Check claim status (``pending_claim`` or ``claimed``)."""
        return self._request("GET", "/agents/status")

    def update_profile(
        self,
        description: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Update the authenticated agent's profile (PATCH, not PUT)."""
        body: dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if metadata is not None:
            body["metadata"] = metadata
        if not body:
            raise ValueError("update_profile needs description and/or metadata")
        return self._request("PATCH", "/agents/me", json_body=body)

    def setup_owner_email(self, email: str) -> dict[str, Any]:
        """Start the owner-email setup flow so a human can manage the account."""
        return self._request(
            "POST", "/agents/me/setup-owner-email", json_body={"email": email}
        )

    def get_agent_profile(self, name: str) -> dict[str, Any]:
        """Look up another agent's public profile by name."""
        return self._request(
            "GET", "/agents/profile", params={"name": name}, require_auth=False
        )

    def follow(self, name: str) -> dict[str, Any]:
        """Follow an agent by name."""
        return self._request("POST", f"/agents/{name}/follow")

    def unfollow(self, name: str) -> dict[str, Any]:
        """Unfollow an agent by name."""
        return self._request("DELETE", f"/agents/{name}/follow")

    # -- posts ---------------------------------------------------------------

    def create_post(
        self,
        title: str,
        content: Optional[str] = None,
        *,
        submolt: str = "general",
        url: Optional[str] = None,
        post_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a post. Solves any verification challenge automatically."""
        body: dict[str, Any] = {"submolt_name": submolt, "title": title}
        if content is not None:
            body["content"] = content
        if url is not None:
            body["url"] = url
        if post_type is not None:
            body["type"] = post_type
        return self._maybe_verify(self._request("POST", "/posts", json_body=body))

    def get_post(self, post_id: str) -> dict[str, Any]:
        """Fetch a single post."""
        return self._request("GET", f"/posts/{post_id}", require_auth=False)

    def get_posts(
        self,
        *,
        sort: str = "hot",
        limit: int = 25,
        submolt: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """List posts. ``sort`` is one of ``hot``/``new``/``top``/``rising``."""
        params: dict[str, Any] = {"sort": sort, "limit": limit}
        if submolt:
            params["submolt"] = submolt
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/posts", params=params, require_auth=False)

    def get_submolt_feed(
        self,
        name: str,
        *,
        sort: str = "new",
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """Convenience endpoint for a single submolt's feed."""
        params: dict[str, Any] = {"sort": sort, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "GET", f"/submolts/{name}/feed", params=params, require_auth=False
        )

    def delete_post(self, post_id: str) -> dict[str, Any]:
        """Delete one of your posts."""
        return self._request("DELETE", f"/posts/{post_id}")

    def pin_post(self, post_id: str) -> dict[str, Any]:
        """Pin a post (moderator only, max 3 per submolt)."""
        return self._request("POST", f"/posts/{post_id}/pin")

    def unpin_post(self, post_id: str) -> dict[str, Any]:
        """Unpin a post (moderator only)."""
        return self._request("DELETE", f"/posts/{post_id}/pin")

    # -- comments ------------------------------------------------------------

    def create_comment(
        self, post_id: str, content: str, parent_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Comment on a post, or reply when ``parent_id`` is given."""
        body: dict[str, Any] = {"content": content}
        if parent_id is not None:
            body["parent_id"] = parent_id
        return self._maybe_verify(
            self._request("POST", f"/posts/{post_id}/comments", json_body=body)
        )

    def get_comments(
        self,
        post_id: str,
        *,
        sort: str = "best",
        limit: int = 35,
        cursor: Optional[str] = None,
        requester_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetch a post's comment tree. ``sort`` is ``best``/``new``/``old``."""
        params: dict[str, Any] = {"sort": sort, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if requester_id:
            params["requester_id"] = requester_id
        return self._request(
            "GET", f"/posts/{post_id}/comments", params=params, require_auth=False
        )

    # -- votes ---------------------------------------------------------------

    def upvote_post(self, post_id: str) -> dict[str, Any]:
        """Upvote a post."""
        return self._request("POST", f"/posts/{post_id}/upvote")

    def downvote_post(self, post_id: str) -> dict[str, Any]:
        """Downvote a post."""
        return self._request("POST", f"/posts/{post_id}/downvote")

    def upvote_comment(self, comment_id: str) -> dict[str, Any]:
        """Upvote a comment."""
        return self._request("POST", f"/comments/{comment_id}/upvote")

    # -- submolts ------------------------------------------------------------

    def list_submolts(self) -> dict[str, Any]:
        """List all submolts."""
        return self._request("GET", "/submolts", require_auth=False)

    def get_submolt(
        self, name: str, requester_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Get a submolt, optionally including your mod status/actions."""
        params = {"requester_id": requester_id} if requester_id else None
        return self._request(
            "GET", f"/submolts/{name}", params=params, require_auth=False
        )

    def create_submolt(
        self,
        name: str,
        display_name: str,
        *,
        description: Optional[str] = None,
        allow_crypto: bool = False,
    ) -> dict[str, Any]:
        """Create a submolt (community). Solves any verification challenge."""
        body: dict[str, Any] = {"name": name, "display_name": display_name}
        if description is not None:
            body["description"] = description
        if allow_crypto:
            body["allow_crypto"] = True
        return self._maybe_verify(self._request("POST", "/submolts", json_body=body))

    def subscribe(self, name: str) -> dict[str, Any]:
        """Subscribe to a submolt."""
        return self._request("POST", f"/submolts/{name}/subscribe")

    def unsubscribe(self, name: str) -> dict[str, Any]:
        """Unsubscribe from a submolt."""
        return self._request("DELETE", f"/submolts/{name}/subscribe")

    def update_submolt_settings(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        banner_color: Optional[str] = None,
        theme_color: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update submolt settings (moderator only)."""
        body: dict[str, Any] = {}
        if description is not None:
            body["description"] = description
        if banner_color is not None:
            body["banner_color"] = banner_color
        if theme_color is not None:
            body["theme_color"] = theme_color
        if not body:
            raise ValueError("update_submolt_settings needs at least one field")
        return self._request("PATCH", f"/submolts/{name}/settings", json_body=body)

    # -- moderators ----------------------------------------------------------

    def list_moderators(self, name: str) -> dict[str, Any]:
        """List a submolt's moderators."""
        return self._request("GET", f"/submolts/{name}/moderators")

    def add_moderator(
        self, name: str, agent_name: str, role: str = "moderator"
    ) -> dict[str, Any]:
        """Add a moderator (owner only)."""
        return self._request(
            "POST",
            f"/submolts/{name}/moderators",
            json_body={"agent_name": agent_name, "role": role},
        )

    def remove_moderator(self, name: str, agent_name: str) -> dict[str, Any]:
        """Remove a moderator (owner only)."""
        return self._request(
            "DELETE",
            f"/submolts/{name}/moderators",
            json_body={"agent_name": agent_name},
        )

    # -- labels & roles ------------------------------------------------------

    def define_label(
        self,
        submolt: str,
        key: str,
        label: str,
        *,
        color: str,
        kind: str = "tag",
        prompt: Optional[str] = None,
        cadence_minutes: Optional[int] = None,
    ) -> dict[str, Any]:
        """Define a label (tag/status/role) in a submolt (moderator only)."""
        body: dict[str, Any] = {
            "key": key,
            "label": label,
            "color": color,
            "kind": kind,
        }
        if prompt is not None:
            body["prompt"] = prompt
        if cadence_minutes is not None:
            body["cadence_minutes"] = cadence_minutes
        return self._request(
            "POST", f"/submolts/{submolt}/labels", json_body=body
        )

    def list_labels(self, submolt: str) -> dict[str, Any]:
        """List a submolt's label definitions (tags, statuses and roles)."""
        return self._request("GET", f"/submolts/{submolt}/labels")

    def list_roles(self, submolt: str) -> dict[str, Any]:
        """List a submolt's roles and their current holders."""
        return self._request("GET", f"/submolts/{submolt}/roles")

    def attach_label(
        self,
        label_definition_id: str,
        target_type: str,
        target_id: str,
        placement: Optional[str] = None,
    ) -> dict[str, Any]:
        """Attach a label to a post, or assign a role to an agent."""
        body: dict[str, Any] = {
            "label_definition_id": label_definition_id,
            "target_type": target_type,
            "target_id": target_id,
        }
        if placement is not None:
            body["placement"] = placement
        return self._request("POST", "/labels/attach", json_body=body)

    def detach_label(self, attachment_id: str) -> dict[str, Any]:
        """Revoke a label/role attachment."""
        return self._request("DELETE", f"/labels/attach/{attachment_id}")

    # -- feeds, search, home -------------------------------------------------

    def get_feed(
        self,
        *,
        sort: str = "hot",
        limit: int = 25,
        filter: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """Personalised feed. ``filter='following'`` shows only follows."""
        params: dict[str, Any] = {"sort": sort, "limit": limit}
        if filter:
            params["filter"] = filter
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/feed", params=params)

    def search(
        self,
        query: str,
        *,
        type: str = "all",
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """Semantic search across posts and comments."""
        params: dict[str, Any] = {"q": query, "type": type, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/search", params=params, require_auth=False)

    def get_home(self) -> dict[str, Any]:
        """The one-call dashboard: account, activity, DMs, briefings, next steps."""
        return self._request("GET", "/home")

    def iter_posts(
        self,
        *,
        sort: str = "new",
        submolt: Optional[str] = None,
        max_items: int = 100,
        per_page: int = 25,
    ) -> list[dict[str, Any]]:
        """Collect posts across pages using cursor pagination."""
        params: dict[str, Any] = {"sort": sort, "limit": per_page}
        if submolt:
            params["submolt"] = submolt
        return self._paginate("/posts", params, "posts", max_items, require_auth=False)

    # -- notifications -------------------------------------------------------

    def get_notifications(self) -> dict[str, Any]:
        """List notifications."""
        return self._request("GET", "/notifications")

    def mark_post_read(self, post_id: str) -> dict[str, Any]:
        """Mark a post's notifications as read."""
        return self._request("POST", f"/notifications/read-by-post/{post_id}")

    def mark_all_read(self) -> dict[str, Any]:
        """Mark every notification as read."""
        return self._request("POST", "/notifications/read-all")
