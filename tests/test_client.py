"""Tests for the Moltbook HTTP client (offline, via FakeSession)."""

from __future__ import annotations

import pytest
import requests

from conftest import FakeResponse, FakeSession
from moltbook_client import (
    DEFAULT_BASE_URL,
    MoltbookAuthError,
    MoltbookClient,
    MoltbookError,
    MoltbookRateLimitError,
    MoltbookVerificationError,
)

DOC_EXAMPLE = "A lobster swims at twenty meters and slows by five, what is the new speed?"


def make_client(responses, api_key: "str | None" = "moltbook_test", **kwargs):
    session = FakeSession(responses)
    client = MoltbookClient(api_key=api_key, session=session, **kwargs)
    return client, session


# -- construction ------------------------------------------------------------


def test_base_url_defaults_and_www_rewrite():
    assert MoltbookClient(api_key="k").base_url == DEFAULT_BASE_URL
    client = MoltbookClient(base_url="https://moltbook.com/api/v1", api_key="k")
    assert client.base_url == "https://www.moltbook.com/api/v1"


def test_missing_api_key_raises_without_request():
    client, session = make_client([], api_key=None)
    with pytest.raises(MoltbookAuthError):
        client.me()
    assert session.calls == []


def test_public_endpoints_work_without_api_key():
    client, session = make_client(
        [FakeResponse(200, {"success": True, "posts": []})], api_key=None
    )
    client.get_posts(sort="new")
    assert "Authorization" not in session.calls[0]["kwargs"]["headers"]


def test_register_works_without_api_key():
    client, session = make_client(
        [FakeResponse(200, {"agent": {"api_key": "x"}})], api_key=None
    )
    client.register_agent("Bot", "does things")
    headers = session.calls[0]["kwargs"]["headers"]
    assert "Authorization" not in headers
    assert session.calls[0]["kwargs"]["json"] == {"name": "Bot", "description": "does things"}


# -- transport ---------------------------------------------------------------


def test_auth_header_and_params_not_in_url():
    client, session = make_client([FakeResponse(200, {"success": True, "posts": []})])
    client.get_posts(sort="new", limit=5, submolt="general")
    call = session.calls[0]
    assert call["kwargs"]["headers"]["Authorization"] == "Bearer moltbook_test"
    assert call["kwargs"]["params"] == {"sort": "new", "limit": 5, "submolt": "general"}
    assert "?" not in call["url"]


def test_401_raises_auth_error_with_api_message():
    body = {"statusCode": 401, "message": "No API key provided", "hint": "Include it"}
    client, _ = make_client([FakeResponse(401, body)], max_retries=1)
    with pytest.raises(MoltbookAuthError, match="No API key provided"):
        client.me()


def test_404_raises_generic_error():
    client, _ = make_client([FakeResponse(404, {"message": "Not found"})], max_retries=1)
    with pytest.raises(MoltbookError, match="Not found"):
        client.get_post("missing")


def test_429_raises_rate_limit_with_retry_after():
    client, _ = make_client(
        [FakeResponse(429, {"message": "slow down"}, headers={"Retry-After": "45"})],
        max_retries=1,
    )
    with pytest.raises(MoltbookRateLimitError) as exc:
        client.get_feed()
    assert exc.value.retry_after == 45.0


def test_429_is_retried_then_succeeds():
    client, session = make_client(
        [
            FakeResponse(429, {"message": "slow"}, headers={"Retry-After": "0"}),
            FakeResponse(200, {"success": True, "feed": []}),
        ],
        max_retries=3,
        backoff_base=0,
    )
    assert client.get_feed()["success"] is True
    assert len(session.calls) == 2


def test_5xx_is_retried_then_succeeds():
    client, session = make_client(
        [FakeResponse(500, {"message": "boom"}), FakeResponse(200, {"success": True})],
        max_retries=3,
        backoff_base=0,
    )
    assert client.get_home()["success"] is True
    assert len(session.calls) == 2


def test_network_error_is_wrapped():
    client, _ = make_client(
        [requests.ConnectionError("dns fail")], max_retries=1
    )
    with pytest.raises(MoltbookError, match="failed"):
        client.get_home()


# -- posts -------------------------------------------------------------------


def test_create_post_uses_submolt_name():
    client, session = make_client([FakeResponse(200, {"success": True, "post": {"id": "p"}})])
    client.create_post(title="Hi", content="Body", submolt="general")
    body = session.calls[0]["kwargs"]["json"]
    assert body["submolt_name"] == "general"
    assert "submolt" not in body
    assert body["title"] == "Hi"


def test_create_post_auto_verifies_challenge():
    create = FakeResponse(
        200,
        {
            "success": True,
            "post": {
                "id": "p1",
                "verification_status": "pending",
                "verification": {
                    "verification_code": "moltbook_verify_abc",
                    "challenge_text": DOC_EXAMPLE,
                },
            },
        },
    )
    verify = FakeResponse(200, {"success": True, "content_id": "p1"})
    client, session = make_client([create, verify])
    response = client.create_post(title="Hi", content="Body")

    assert response["verified"] is True
    assert len(session.calls) == 2
    assert session.calls[1]["url"].endswith("/verify")
    assert session.calls[1]["kwargs"]["json"] == {
        "verification_code": "moltbook_verify_abc",
        "answer": "15.00",
    }


def test_create_post_skips_verify_when_disabled():
    create = FakeResponse(
        200,
        {"post": {"id": "p1", "verification": {"verification_code": "c", "challenge_text": DOC_EXAMPLE}}},
    )
    client, session = make_client([create], auto_verify=False)
    client.create_post(title="Hi", content="Body")
    assert len(session.calls) == 1


def test_create_post_unsolvable_raises_verification_error():
    create = FakeResponse(
        200,
        {"post": {"id": "p1", "verification": {"verification_code": "c", "challenge_text": "unsolvable prose"}}},
    )
    client, session = make_client([create])
    with pytest.raises(MoltbookVerificationError):
        client.create_post(title="Hi", content="Body")
    # No /verify call must be attempted with a guessed answer.
    assert len(session.calls) == 1


def test_custom_solver_is_used():
    create = FakeResponse(
        200,
        {"post": {"id": "p1", "verification": {"verification_code": "c", "challenge_text": "anything"}}},
    )
    verify = FakeResponse(200, {"success": True})
    client, session = make_client([create, verify], solver=lambda _c: 99)
    response = client.create_post(title="Hi", content="Body")
    assert response["verified"] is True
    assert session.calls[1]["kwargs"]["json"]["answer"] == "99.00"


# -- pagination --------------------------------------------------------------


def test_iter_posts_follows_cursor():
    page1 = FakeResponse(200, {"posts": [{"id": "a"}], "has_more": True, "next_cursor": "c1"})
    page2 = FakeResponse(200, {"posts": [{"id": "b"}], "has_more": False})
    client, session = make_client([page1, page2])
    posts = client.iter_posts(sort="new", max_items=10)

    assert [p["id"] for p in posts] == ["a", "b"]
    assert len(session.calls) == 2
    assert session.calls[0]["kwargs"]["params"].get("cursor") is None
    assert session.calls[1]["kwargs"]["params"]["cursor"] == "c1"


def test_iter_posts_stops_at_max_items():
    page = FakeResponse(200, {"posts": [{"id": "a"}, {"id": "b"}], "has_more": True, "next_cursor": "c"})
    client, session = make_client([page])
    posts = client.iter_posts(max_items=2)
    assert len(posts) == 2
    assert len(session.calls) == 1


# -- misc endpoints ----------------------------------------------------------


def test_get_comments_passes_requester_id():
    client, session = make_client([FakeResponse(200, {"success": True, "comments": []})])
    client.get_comments("p1", sort="new", limit=10, requester_id="agent-1")
    params = session.calls[0]["kwargs"]["params"]
    assert params == {"sort": "new", "limit": 10, "requester_id": "agent-1"}


def test_context_manager_closes_owned_session():
    client = MoltbookClient(api_key="k")
    with client as c:
        assert c is client
    assert client.session is not None
