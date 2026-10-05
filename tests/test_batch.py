"""Tests for batch content publishing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch_content import BatchContent, create_sample_batch_file
from conftest import FakeBatchClient
from moltbook_client import MoltbookError, MoltbookRateLimitError

POSTS = [
    {"submolt": "general", "title": "One", "content": "Body one"},
    {"submolt": "agents", "title": "Two", "content": "Body two"},
]


def make_batch(responses: list[Any]) -> tuple[BatchContent, FakeBatchClient]:
    """BatchContent only calls ``create_post``; the fake satisfies it at runtime."""
    client = FakeBatchClient(responses)
    return BatchContent(client), client  # type: ignore[arg-type]


def test_load_batch_file_list(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(POSTS), encoding="utf-8")
    batch, _ = make_batch([])
    assert batch.load_batch_file(str(path)) == POSTS


def test_load_batch_file_dict(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"posts": POSTS}), encoding="utf-8")
    batch, _ = make_batch([])
    assert batch.load_batch_file(str(path)) == POSTS


def test_load_batch_file_missing():
    batch, _ = make_batch([])
    with pytest.raises(FileNotFoundError):
        batch.load_batch_file("/nonexistent/batch.json")


def test_load_batch_file_bad_shape(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    batch, _ = make_batch([])
    with pytest.raises(ValueError):
        batch.load_batch_file(str(path))


def test_validate_posts_flags_missing_fields():
    batch, _ = make_batch([])
    valid, errors = batch.validate_posts(
        [{"title": "ok", "content": "x"}, {"title": "", "content": "x"}, {"title": "y"}]
    )
    assert valid is False
    assert len(errors) == 2


def test_publish_batch_dry_run_makes_no_calls():
    batch, client = make_batch([])
    results = batch.publish_batch(POSTS, dry_run=True)
    assert [r["status"] for r in results] == ["dry-run", "dry-run"]
    assert client.calls == []


def test_publish_batch_success():
    batch, client = make_batch(
        [
            {"post": {"id": "p1"}, "verified": True},
            {"post": {"id": "p2"}, "verified": False},
        ]
    )
    results = batch.publish_batch(POSTS)
    assert [r["status"] for r in results] == ["success", "success"]
    assert results[0]["post_id"] == "p1"
    assert results[0]["verified"] is True
    assert client.calls[0]["submolt"] == "general"
    assert client.calls[1]["submolt"] == "agents"


def test_publish_batch_accepts_submolt_name_key():
    batch, client = make_batch([{"post": {"id": "p1"}}])
    batch.publish_batch([{"submolt_name": "agents", "title": "T", "content": "C"}])
    assert client.calls[0]["submolt"] == "agents"


def test_publish_batch_rate_limited():
    batch, _ = make_batch([MoltbookRateLimitError("slow", retry_after=30)])
    results = batch.publish_batch(POSTS[:1])
    assert results[0]["status"] == "rate-limited"
    assert results[0]["retry_after"] == 30


def test_publish_batch_error():
    batch, _ = make_batch([MoltbookError("boom")])
    results = batch.publish_batch(POSTS[:1])
    assert results[0]["status"] == "error"
    assert "boom" in results[0]["error"]


def test_publish_batch_invalid_posts_short_circuits():
    batch, client = make_batch([])
    results = batch.publish_batch([{"title": "no content"}])
    assert results[0]["status"] == "error"
    assert client.calls == []


def test_create_sample_batch_file(tmp_path):
    path = tmp_path / "sample.json"
    create_sample_batch_file(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "posts" in data and len(data["posts"]) == 3
