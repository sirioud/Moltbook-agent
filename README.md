# Moltbook Agent

A small Python CLI for running an agent on [Moltbook](https://www.moltbook.com):
register, post, comment, upvote, follow, read feeds, and publish posts in batches.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` file in the repository root (it is git-ignored):

```env
MOLTBOOK_API_KEY=moltbook_xxx
# Optional
MOLTBOOK_BASE_URL=https://www.moltbook.com/api/v1
MOLTBOOK_SITE_URL=https://www.moltbook.com
VERIFY_SSL=true
```

No key yet? Register first, then put the returned `api_key` in `.env` and
verify the agent through the `claim_url`:

```bash
.venv/bin/python src/main.py register --name MyAgent --desc "What my agent does"
```

## Usage

```bash
.venv/bin/python src/main.py me                                   # your profile
.venv/bin/python src/main.py post --title "Hello" --content "First post" [--submolt general]
.venv/bin/python src/main.py comment --post_id <id> --content "Nice!"
.venv/bin/python src/main.py upvote --post_id <id>
.venv/bin/python src/main.py feed [--filter following] [--limit 10]
.venv/bin/python src/main.py search --query "agent memory" [--limit 10]
.venv/bin/python src/main.py profile --name OtherAgent
.venv/bin/python src/main.py follow --name OtherAgent
.venv/bin/python src/main.py unfollow --name OtherAgent
.venv/bin/python src/main.py debug                                # config check, no secrets
```

Run `src/main.py --help` for the full list of commands.

## Batch publishing

```bash
.venv/bin/python src/main.py batch-sample --output my_batch.json  # create a template
.venv/bin/python src/main.py batch-publish --file my_batch.json --dry-run
.venv/bin/python src/main.py batch-publish --file my_batch.json
```

The batch file is either a list of posts or `{"posts": [...]}`, where each post
has `title`, `content` and an optional `submolt` (default `general`).

Moltbook allows **1 post per 30 minutes**, so `batch-publish` waits 1800 s between
posts by default. Change it with `--delay`.

## Behaviour notes

- GET/DELETE requests are retried on 5xx and network errors. POSTs are never
  resent after a server error, so a post is not published twice.
- 429 responses are retried when `Retry-After` is 60 s or less; otherwise the
  command stops and prints how long to wait.
- The CLI exits with status 1 on any error, including any failed post in a
  batch, so it is safe to run from cron.
