import os
import json
import argparse
from pathlib import Path

from dotenv import load_dotenv

# Always load .env from the repository root
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT / ".env")

from moltbook_client import MoltbookClient, MoltbookError  # noqa: E402
from batch_content import BatchContent, create_sample_batch_file  # noqa: E402


def _site_url() -> str:
    return os.getenv("MOLTBOOK_SITE_URL", "https://www.moltbook.com").rstrip("/")


def _print_post_summary(resp: dict) -> None:
    """
    Moltbook often returns:
      {"success": true, "message": "...", "post": {...}}
    but sometimes you may just get the post dict.
    """
    post = resp.get("post") if isinstance(resp, dict) else None
    if not isinstance(post, dict):
        # fallback: maybe resp is already the post object
        post = resp if isinstance(resp, dict) else {}

    post_id = post.get("id")
    path = post.get("url")  # e.g. "/post/<uuid>"
    title = post.get("title")

    if path:
        full = _site_url() + path
        print(f"\n🔗 Post link: {full}")
    if post_id:
        print(f"🆔 Post ID:   {post_id}")
    if title:
        print(f"📝 Title:     {title}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Moltbook Agent CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="Register a new agent (returns api_key + claim info)")
    p_reg.add_argument("--name", required=True)
    p_reg.add_argument("--desc", required=True)

    sub.add_parser("me", help="Fetch the authenticated agent profile")

    p_post = sub.add_parser("post", help="Create a post (requires verified + api key)")
    p_post.add_argument("--submolt", default="general")
    p_post.add_argument("--title", required=True)
    p_post.add_argument("--content", required=True)

    p_comment = sub.add_parser("comment", help="Comment on a post")
    p_comment.add_argument("--post_id", required=True)
    p_comment.add_argument("--content", required=True)

    p_upvote = sub.add_parser("upvote", help="Upvote a post")
    p_upvote.add_argument("--post_id", required=True)

    sub.add_parser("home", help="Get home feed with posts from followed agents")

    p_posts = sub.add_parser("posts", help="List posts")
    p_posts.add_argument("--submolt", help="Filter by submolt")
    p_posts.add_argument("--limit", type=int, default=10, help="Number of posts to fetch")

    p_agents = sub.add_parser("agents", help="List agents")
    p_agents.add_argument("--limit", type=int, default=10, help="Number of agents to fetch")

    p_profile = sub.add_parser("profile", help="Get agent profile")
    p_profile.add_argument("--agent_id", required=True)

    p_follow = sub.add_parser("follow", help="Follow an agent")
    p_follow.add_argument("--agent_id", required=True)

    p_unfollow = sub.add_parser("unfollow", help="Unfollow an agent")
    p_unfollow.add_argument("--agent_id", required=True)

    p_feed = sub.add_parser("feed", help="Get general feed")
    p_feed.add_argument("--filter", choices=["following"], help="Filter type")
    p_feed.add_argument("--limit", type=int, default=10, help="Number of posts")

    p_search = sub.add_parser("search", help="Search posts")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=10)

    sub.add_parser("submolts", help="List submolts")

    p_get_post = sub.add_parser("get-post", help="Get post details")
    p_get_post.add_argument("--id", required=True, help="Post ID to get details")

    p_batch_sample = sub.add_parser("batch-sample", help="Create a sample batch file")
    p_batch_sample.add_argument("--output", default="batch_sample.json", help="Output file path")

    p_batch_publish = sub.add_parser("batch-publish", help="Publish posts from a batch file")
    p_batch_publish.add_argument("--file", required=True, help="Batch JSON file path")
    p_batch_publish.add_argument("--delay", type=float, default=0, help="Delay between posts in seconds")
    p_batch_publish.add_argument("--dry-run", action="store_true", help="Preview without posting")

    sub.add_parser("debug", help="Print env + computed URLs (no secrets)")

    args = parser.parse_args()

    base_url = os.getenv("MOLTBOOK_BASE_URL", "https://www.moltbook.com/api/v1").rstrip("/")
    api_key = os.getenv("MOLTBOOK_API_KEY") or ""
    verify_ssl = os.getenv("VERIFY_SSL", "true").lower() != "false"

    if args.cmd == "debug":
        print("Repo root:", str(ROOT))
        print("MOLTBOOK_BASE_URL =", repr(base_url))
        print("Computed /agents/me =", base_url + "/agents/me")
        print("MOLTBOOK_SITE_URL =", repr(_site_url()))
        print("API key prefix/len =", api_key[:10], len(api_key))
        return

    client = MoltbookClient(base_url=base_url, api_key=api_key or None, verify_ssl=verify_ssl)

    try:
        if args.cmd == "register":
            data = client.register_agent(args.name, args.desc)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            if "api_key" in data:
                print("\nNext: copy api_key into .env as MOLTBOOK_API_KEY, then verify via claim_url.")

        elif args.cmd == "me":
            print(json.dumps(client.me(), indent=2, ensure_ascii=False))

        elif args.cmd == "post":
            resp = client.create_post(args.submolt, args.title, args.content)
            print(json.dumps(resp, indent=2, ensure_ascii=False))
            _print_post_summary(resp)

        elif args.cmd == "comment":
            resp = client.comment(args.post_id, args.content)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "upvote":
            resp = client.upvote(args.post_id)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "home":
            resp = client.get_home_feed()
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "posts":
            resp = client.get_posts(submolt=getattr(args, 'submolt', None), limit=getattr(args, 'limit', 10))
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "agents":
            resp = client.get_agents(limit=getattr(args, 'limit', 10))
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "profile":
            resp = client.get_agent_profile(args.agent_id)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "follow":
            resp = client.follow_agent(args.agent_id)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "unfollow":
            resp = client.unfollow_agent(args.agent_id)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "feed":
            filter_type = getattr(args, 'filter', None)
            limit = getattr(args, 'limit', 10)
            resp = client.get_feed(filter_type=filter_type, limit=limit)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "search":
            resp = client.search_posts(args.query, limit=getattr(args, 'limit', 10))
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "submolts":
            resp = client.get_submolts()
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "get-post":
            resp = client.get_post(args.id)
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        elif args.cmd == "batch-sample":
            create_sample_batch_file(args.output)

        elif args.cmd == "batch-publish":
            batch = BatchContent(client)
            posts = batch.load_batch_file(args.file)
            batch.publish_batch(posts, delay_seconds=args.delay, dry_run=args.dry_run)
            batch.print_summary()

    except MoltbookError as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
