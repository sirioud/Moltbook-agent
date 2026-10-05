"""Batch content publishing for Moltbook agents.

Publishes a JSON file of posts sequentially, optionally pacing them to respect
Moltbook's posting cooldowns. Each post goes through the same verification flow
as a single post (handled by the client), and rate limits are honoured rather
than retried blindly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from moltbook_client import MoltbookClient, MoltbookError, MoltbookRateLimitError

__all__ = ["BatchContent", "create_sample_batch_file"]


class BatchContent:
    """Publish a batch of posts through a :class:`MoltbookClient`."""

    def __init__(self, client: MoltbookClient) -> None:
        self.client = client
        self.results: list[dict[str, Any]] = []

    def load_batch_file(self, filepath: str) -> list[dict[str, Any]]:
        """Load posts from a JSON file (a list, or ``{"posts": [...]}``)."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Batch file not found: {filepath}")
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and "posts" in data:
            posts = data["posts"]
        elif isinstance(data, list):
            posts = data
        else:
            raise ValueError(
                'Batch file must contain a list of posts or {"posts": [...]}'
            )
        if not isinstance(posts, list):
            raise ValueError('"posts" must be a list')
        return posts

    def validate_posts(self, posts: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        """Check every post has a non-empty title and content."""
        errors: list[str] = []
        for index, post in enumerate(posts):
            if not isinstance(post, dict):
                errors.append(f"Post {index}: not an object")
                continue
            title = post.get("title")
            content = post.get("content")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"Post {index}: title must be a non-empty string")
            if not isinstance(content, str) or not content.strip():
                errors.append(f"Post {index}: content must be a non-empty string")
        return len(errors) == 0, errors

    def publish_batch(
        self,
        posts: list[dict[str, Any]],
        delay_seconds: float = 0,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Publish ``posts``, returning one result dict per post.

        Args:
            posts: Post dicts with ``title``, ``content`` and optional ``submolt``.
            delay_seconds: Pause between posts (in addition to any rate-limit wait).
            dry_run: Preview without posting.
        """
        self.results = []
        valid, errors = self.validate_posts(posts)
        if not valid:
            self.results = [{"status": "error", "message": err} for err in errors]
            return self.results

        for index, post in enumerate(posts):
            title = post["title"]
            content = post["content"]
            submolt = post.get("submolt") or post.get("submolt_name") or "general"

            if dry_run:
                self.results.append(
                    {
                        "status": "dry-run",
                        "index": index,
                        "submolt": submolt,
                        "title": title,
                        "message": f"Would publish to {submolt}",
                    }
                )
                print(f"\n[{index + 1}/{len(posts)}] DRY RUN: {title}")
            else:
                self.results.append(
                    self._publish_one(index, len(posts), submolt, title, content)
                )

            if delay_seconds > 0 and index < len(posts) - 1:
                time.sleep(delay_seconds)

        return self.results

    def _publish_one(
        self,
        index: int,
        total: int,
        submolt: str,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        """Publish a single post, mapping failures onto a result dict."""
        print(f"\n[{index + 1}/{total}] Publishing: {title}...", end=" ", flush=True)
        try:
            response = self.client.create_post(
                title=title, content=content, submolt=submolt
            )
        except MoltbookRateLimitError as exc:
            print(f"rate limited (retry after {exc.retry_after}s)")
            return {
                "status": "rate-limited",
                "index": index,
                "title": title,
                "submolt": submolt,
                "retry_after": exc.retry_after,
                "error": str(exc),
            }
        except MoltbookError as exc:
            print(f"error: {exc}")
            return {
                "status": "error",
                "index": index,
                "title": title,
                "submolt": submolt,
                "error": str(exc),
            }

        post_node = response.get("post")
        post = post_node if isinstance(post_node, dict) else {}
        verified = response.get("verified")
        print("posted" + ("" if verified is None else f" (verified={verified})"))
        return {
            "status": "success",
            "index": index,
            "title": title,
            "submolt": submolt,
            "post_id": post.get("id"),
            "verified": verified,
        }

    def print_summary(self) -> None:
        """Print a tally of the last batch run."""
        if not self.results:
            return
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.get("status", "unknown")] = (
                counts.get(result.get("status", "unknown"), 0) + 1
            )

        print("\n" + "=" * 60)
        print("BATCH OPERATION SUMMARY")
        print("=" * 60)
        for status in ("success", "dry-run", "rate-limited", "error"):
            if counts.get(status):
                print(f"{status:<14} {counts[status]}")
        print("=" * 60)


def create_sample_batch_file(filepath: str = "batch_sample.json") -> None:
    """Write a sample batch file and print how to publish it."""
    sample = {
        "posts": [
            {
                "submolt": "general",
                "title": "My First Batch Post",
                "content": (
                    "This is the first post in a batch. It's a great way to "
                    "share multiple thoughts at once!"
                ),
            },
            {
                "submolt": "general",
                "title": "Second Batch Post",
                "content": (
                    "Batch posting lets you publish multiple posts efficiently. "
                    "Each post goes through the same flow but automatically."
                ),
            },
            {
                "submolt": "general",
                "title": "Building Community Through Automation",
                "content": (
                    "Automated posting is perfect for sharing research, updates, "
                    "or regular content. You can schedule these batches on a "
                    "cron job for continuous engagement!"
                ),
            },
        ]
    }
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, indent=2, ensure_ascii=False)
    print(f"Sample batch file created: {filepath}")
    print("Edit this file to customize your posts, then run:")
    print(f"  python src/main.py batch-publish --file {filepath}")
