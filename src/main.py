#!/usr/bin/env python3
"""Moltbook Agent CLI.

A command-line front end for :mod:`moltbook_client`, covering the current
Moltbook API surface (spec ``skill.md`` v1.12.0): agents, posts, comments,
votes, submolts, feeds, semantic search, notifications, labels/roles and
verification challenges.

Configuration is read from a ``.env`` at the repository root:

    MOLTBOOK_BASE_URL   optional, defaults to https://www.moltbook.com/api/v1
    MOLTBOOK_API_KEY    your agent key (moltbook_...), required for auth calls
    MOLTBOOK_SITE_URL   optional, used to print full post links
    VERIFY_SSL          optional, "false" disables TLS verification (don't)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# Make the src/ directory importable when run as `python src/main.py`.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

load_dotenv(dotenv_path=ROOT / ".env")

from batch_content import BatchContent, create_sample_batch_file  # noqa: E402
from moltbook_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    MoltbookAuthError,
    MoltbookClient,
    MoltbookError,
    MoltbookRateLimitError,
    MoltbookVerificationError,
)

VERSION = "2.0.0"


def _site_url() -> str:
    return os.getenv("MOLTBOOK_SITE_URL", "https://www.moltbook.com").rstrip("/")


def _dump(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_post_summary(response: dict[str, Any]) -> None:
    """Print a post link/id/title from a create-post response."""
    node = response.get("post")
    post = node if isinstance(node, dict) else response
    path = post.get("url")
    post_id = post.get("id")
    title = post.get("title")
    if path:
        print(f"\nPost link: {_site_url()}{path}")
    if post_id:
        print(f"Post ID:   {post_id}")
    if title:
        print(f"Title:     {title}")
    if response.get("verification_result") is not None:
        print(f"Verified:  {response.get('verified')}")


def _parse_metadata(raw: Optional[str]) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--metadata must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("--metadata must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moltbook", description="Moltbook Agent CLI")
    parser.add_argument("--base-url", default=None, help="Override the API base URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="Register a new agent (no auth)")
    p.add_argument("--name", required=True)
    p.add_argument("--desc", required=True)

    sub.add_parser("me", help="Fetch your agent profile")
    sub.add_parser("status", help="Check claim status")

    p = sub.add_parser("update-profile", help="Update your description/metadata")
    p.add_argument("--description")
    p.add_argument("--metadata", help="JSON object")

    p = sub.add_parser("setup-owner-email", help="Start owner-email setup")
    p.add_argument("--email", required=True)

    p = sub.add_parser("profile", help="View another agent's profile")
    p.add_argument("--name", required=True)

    p = sub.add_parser("follow", help="Follow an agent")
    p.add_argument("--name", required=True)
    p = sub.add_parser("unfollow", help="Unfollow an agent")
    p.add_argument("--name", required=True)

    p = sub.add_parser("post", help="Create a post")
    p.add_argument("--title", required=True)
    p.add_argument("--content")
    p.add_argument("--submolt", default="general")
    p.add_argument("--url")
    p.add_argument("--type", dest="post_type", choices=["text", "link", "image"])

    p = sub.add_parser("get-post", help="Fetch a post")
    p.add_argument("--id", required=True)

    p = sub.add_parser("posts", help="List posts")
    p.add_argument("--sort", default="hot", choices=["hot", "new", "top", "rising"])
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--submolt")
    p.add_argument("--cursor")
    p.add_argument("--all", type=int, metavar="N", help="Page until N posts collected")

    p = sub.add_parser("submolt-feed", help="Feed for one submolt")
    p.add_argument("--name", required=True)
    p.add_argument("--sort", default="new", choices=["hot", "new", "top", "rising"])
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("delete-post", help="Delete your post")
    p.add_argument("--id", required=True)
    p = sub.add_parser("pin-post", help="Pin a post (moderator)")
    p.add_argument("--id", required=True)
    p = sub.add_parser("unpin-post", help="Unpin a post (moderator)")
    p.add_argument("--id", required=True)

    p = sub.add_parser("comment", help="Comment on a post, or reply")
    p.add_argument("--post_id", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--parent_id")

    p = sub.add_parser("comments", help="List a post's comments")
    p.add_argument("--post_id", required=True)
    p.add_argument("--sort", default="best", choices=["best", "new", "old"])
    p.add_argument("--limit", type=int, default=35)
    p.add_argument("--cursor")
    p.add_argument("--requester_id")

    p = sub.add_parser("upvote", help="Upvote a post")
    p.add_argument("--post_id", required=True)
    p = sub.add_parser("downvote", help="Downvote a post")
    p.add_argument("--post_id", required=True)
    p = sub.add_parser("upvote-comment", help="Upvote a comment")
    p.add_argument("--comment_id", required=True)

    sub.add_parser("submolts", help="List submolts")
    p = sub.add_parser("submolt", help="Get submolt info")
    p.add_argument("--name", required=True)
    p.add_argument("--requester_id")

    p = sub.add_parser("create-submolt", help="Create a submolt")
    p.add_argument("--name", required=True)
    p.add_argument("--display-name", required=True)
    p.add_argument("--description")
    p.add_argument("--allow-crypto", action="store_true")

    p = sub.add_parser("subscribe", help="Subscribe to a submolt")
    p.add_argument("--name", required=True)
    p = sub.add_parser("unsubscribe", help="Unsubscribe from a submolt")
    p.add_argument("--name", required=True)

    p = sub.add_parser("moderators", help="List moderators")
    p.add_argument("--name", required=True)
    p = sub.add_parser("add-moderator", help="Add a moderator (owner)")
    p.add_argument("--name", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--role", default="moderator")
    p = sub.add_parser("remove-moderator", help="Remove a moderator (owner)")
    p.add_argument("--name", required=True)
    p.add_argument("--agent", required=True)

    p = sub.add_parser("labels", help="List a submolt's labels")
    p.add_argument("--name", required=True)
    p = sub.add_parser("roles", help="List a submolt's roles")
    p.add_argument("--name", required=True)
    p = sub.add_parser("define-label", help="Define a label (moderator)")
    p.add_argument("--submolt", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--color", required=True)
    p.add_argument("--kind", default="tag", choices=["tag", "status", "role"])
    p.add_argument("--prompt")
    p.add_argument("--cadence-minutes", type=int)
    p = sub.add_parser("attach-label", help="Attach a label / assign a role")
    p.add_argument("--definition-id", required=True)
    p.add_argument("--target-type", required=True, choices=["post", "agent"])
    p.add_argument("--target-id", required=True)
    p.add_argument("--placement")

    p = sub.add_parser("feed", help="Your personalised feed")
    p.add_argument("--sort", default="hot", choices=["hot", "new", "top"])
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--filter", choices=["all", "following"])
    p.add_argument("--cursor")

    p = sub.add_parser("search", help="Semantic search")
    p.add_argument("--query", required=True)
    p.add_argument("--type", default="all", choices=["posts", "comments", "all"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--cursor")

    sub.add_parser("home", help="One-call dashboard")
    sub.add_parser("notifications", help="List notifications")
    p = sub.add_parser("mark-read", help="Mark a post's notifications read")
    p.add_argument("--post_id", required=True)
    sub.add_parser("mark-all-read", help="Mark every notification read")

    p = sub.add_parser("batch-sample", help="Write a sample batch file")
    p.add_argument("--output", default="batch_sample.json")
    p = sub.add_parser("batch-publish", help="Publish posts from a batch file")
    p.add_argument("--file", required=True)
    p.add_argument("--delay", type=float, default=0)
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("debug", help="Print env + computed URLs (no secrets)")
    sub.add_parser("version", help="Print the client version")
    return parser


def _build_client(args: argparse.Namespace) -> MoltbookClient:
    base_url = args.base_url or os.getenv("MOLTBOOK_BASE_URL", DEFAULT_BASE_URL)
    api_key = os.getenv("MOLTBOOK_API_KEY") or None
    verify_ssl = os.getenv("VERIFY_SSL", "true").lower() != "false"
    return MoltbookClient(
        base_url=base_url, api_key=api_key, verify_ssl=verify_ssl
    )


def _dispatch(args: argparse.Namespace, client: MoltbookClient) -> None:
    cmd = args.cmd

    if cmd == "register":
        data = client.register_agent(args.name, args.desc)
        _dump(data)
        agent_node = data.get("agent")
        agent = agent_node if isinstance(agent_node, dict) else {}
        if agent.get("api_key"):
            print("\nNext: save the api_key as MOLTBOOK_API_KEY, then verify via claim_url.")
        return

    if cmd == "me":
        _dump(client.me())
    elif cmd == "status":
        _dump(client.status())
    elif cmd == "update-profile":
        _dump(client.update_profile(args.description, _parse_metadata(args.metadata)))
    elif cmd == "setup-owner-email":
        _dump(client.setup_owner_email(args.email))
    elif cmd == "profile":
        _dump(client.get_agent_profile(args.name))
    elif cmd == "follow":
        _dump(client.follow(args.name))
    elif cmd == "unfollow":
        _dump(client.unfollow(args.name))

    elif cmd == "post":
        response = client.create_post(
            title=args.title,
            content=args.content,
            submolt=args.submolt,
            url=args.url,
            post_type=args.post_type,
        )
        _dump(response)
        _print_post_summary(response)
    elif cmd == "get-post":
        _dump(client.get_post(args.id))
    elif cmd == "posts":
        if args.all:
            _dump(client.iter_posts(sort=args.sort, submolt=args.submolt, max_items=args.all))
        else:
            _dump(client.get_posts(sort=args.sort, limit=args.limit, submolt=args.submolt, cursor=args.cursor))
    elif cmd == "submolt-feed":
        _dump(client.get_submolt_feed(args.name, sort=args.sort, limit=args.limit))
    elif cmd == "delete-post":
        _dump(client.delete_post(args.id))
    elif cmd == "pin-post":
        _dump(client.pin_post(args.id))
    elif cmd == "unpin-post":
        _dump(client.unpin_post(args.id))

    elif cmd == "comment":
        _dump(client.create_comment(args.post_id, args.content, args.parent_id))
    elif cmd == "comments":
        _dump(client.get_comments(args.post_id, sort=args.sort, limit=args.limit, cursor=args.cursor, requester_id=args.requester_id))

    elif cmd == "upvote":
        _dump(client.upvote_post(args.post_id))
    elif cmd == "downvote":
        _dump(client.downvote_post(args.post_id))
    elif cmd == "upvote-comment":
        _dump(client.upvote_comment(args.comment_id))

    elif cmd == "submolts":
        _dump(client.list_submolts())
    elif cmd == "submolt":
        _dump(client.get_submolt(args.name, args.requester_id))
    elif cmd == "create-submolt":
        _dump(client.create_submolt(args.name, args.display_name, description=args.description, allow_crypto=args.allow_crypto))
    elif cmd == "subscribe":
        _dump(client.subscribe(args.name))
    elif cmd == "unsubscribe":
        _dump(client.unsubscribe(args.name))

    elif cmd == "moderators":
        _dump(client.list_moderators(args.name))
    elif cmd == "add-moderator":
        _dump(client.add_moderator(args.name, args.agent, args.role))
    elif cmd == "remove-moderator":
        _dump(client.remove_moderator(args.name, args.agent))

    elif cmd == "labels":
        _dump(client.list_labels(args.name))
    elif cmd == "roles":
        _dump(client.list_roles(args.name))
    elif cmd == "define-label":
        _dump(client.define_label(args.submolt, args.key, args.label, color=args.color, kind=args.kind, prompt=args.prompt, cadence_minutes=args.cadence_minutes))
    elif cmd == "attach-label":
        _dump(client.attach_label(args.definition_id, args.target_type, args.target_id, args.placement))

    elif cmd == "feed":
        _dump(client.get_feed(sort=args.sort, limit=args.limit, filter=args.filter, cursor=args.cursor))
    elif cmd == "search":
        _dump(client.search(args.query, type=args.type, limit=args.limit, cursor=args.cursor))
    elif cmd == "home":
        _dump(client.get_home())
    elif cmd == "notifications":
        _dump(client.get_notifications())
    elif cmd == "mark-read":
        _dump(client.mark_post_read(args.post_id))
    elif cmd == "mark-all-read":
        _dump(client.mark_all_read())

    elif cmd == "batch-sample":
        create_sample_batch_file(args.output)
    elif cmd == "batch-publish":
        batch = BatchContent(client)
        posts = batch.load_batch_file(args.file)
        batch.publish_batch(posts, delay_seconds=args.delay, dry_run=args.dry_run)
        batch.print_summary()


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "version":
        print(f"moltbook-agent {VERSION}")
        return 0

    base_url = args.base_url or os.getenv("MOLTBOOK_BASE_URL", DEFAULT_BASE_URL)
    if args.cmd == "debug":
        api_key = os.getenv("MOLTBOOK_API_KEY") or ""
        print("Repo root:      ", str(ROOT))
        print("MOLTBOOK_BASE_URL =", repr(base_url))
        print("Computed /home  =", base_url.rstrip("/") + "/home")
        print("MOLTBOOK_SITE_URL =", repr(_site_url()))
        print("API key prefix/len =", api_key[:10], len(api_key))
        return 0

    client = _build_client(args)
    try:
        _dispatch(args, client)
    except MoltbookAuthError as exc:
        print(f"AUTH ERROR: {exc}", file=sys.stderr)
        return 1
    except MoltbookRateLimitError as exc:
        print(f"RATE LIMITED: {exc}", file=sys.stderr)
        return 1
    except MoltbookVerificationError as exc:
        print(f"VERIFICATION ERROR: {exc}", file=sys.stderr)
        return 1
    except MoltbookError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
