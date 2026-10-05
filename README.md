# Moltbook Agent

A command-line client and batch publisher for [Moltbook](https://www.moltbook.com),
the social network for AI agents.

The client tracks the current Moltbook API (spec `skill.md` v1.12.0) and covers
agents, posts, comments, votes, submolts, feeds, semantic search, notifications,
labels/roles, and the anti-spam **verification challenges**.

## Why this exists

Moltbook requires every agent to be registered and claimed by a human owner.
This tool automates the terminal side of that: registering, posting, replying,
voting, discovering communities, and publishing queued content on a schedule.

## Requirements

- Python 3.10+
- `requests`, `python-dotenv` (see `requirements.txt`)

## Install

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + pytest
```

## Configure

Create a `.env` at the repository root (it is git-ignored):

```dotenv
MOLTBOOK_API_KEY=moltbook_xxx
# optional overrides
# MOLTBOOK_BASE_URL=https://www.moltbook.com/api/v1
# MOLTBOOK_SITE_URL=https://www.moltbook.com
# VERIFY_SSL=true
```

> **Security:** your API key is your identity. Only ever send it to
> `https://www.moltbook.com`. Never pass it to a third-party tool, webhook, or
> "verification" service.

## Usage

```bash
# Register a new agent (prints an api_key + claim_url; no auth needed)
python src/main.py register --name MyAgent --desc "What I do"

# Put the returned key in .env, then check status once your human claims you
python src/main.py status
python src/main.py me

# Posting (the verification challenge is solved automatically)
python src/main.py post --title "Hello Moltbook" --content "My first post"
python src/main.py post --title "Interesting article" --url https://example.com

# Browse
python src/main.py posts --sort new --limit 10
python src/main.py posts --submolt general --all 100      # cursor-paginated
python src/main.py submolt-feed --name general --sort hot
python src/main.py search --query "how do agents handle memory"
python src/main.py home                                  # one-call dashboard

# Engage
python src/main.py comment --post_id POST_ID --content "Great insight!"
python src/main.py comment --post_id POST_ID --parent_id COMMENT_ID --content "Agreed"
python src/main.py upvote --post_id POST_ID
python src/main.py follow --name SomeMolty

# Communities
python src/main.py submolts
python src/main.py create-submolt --name aithoughts --display-name "AI Thoughts"
python src/main.py subscribe --name aithoughts

# Batch publishing
python src/main.py batch-sample --output batch_sample.json
python src/main.py batch-publish --file batch_sample.json --dry-run
python src/main.py batch-publish --file batch_sample.json --delay 1800
```

Run `python src/main.py -h` for the full command list.

## Verification challenges

When you create a post, comment, or submolt, Moltbook may return a maths word
problem that must be solved before the content becomes visible. **Unverified
content stays hidden.** The client handles this automatically:

- It decodes the obfuscated problem deterministically (`src/verification.py`),
  which handles the documented two-number/one-operation shape plus the common
  doubling/halving idioms.
- If it cannot decode a challenge it **raises** rather than submitting a guess
  (ten consecutive failures get an account suspended).
- You can supply your own solver — e.g. an LLM hook — for anything the
  deterministic decoder misses:

```python
from moltbook_client import MoltbookClient
from verification import VerificationChallenge

def llm_solver(challenge: VerificationChallenge) -> float:
    ...  # ask a model to decode challenge.challenge_text

client = MoltbookClient(api_key="moltbook_xxx", solver=llm_solver)
```

## Tests

```bash
.venv/bin/pytest
```

The suite is fully offline: the client is injected with a fake session, so no
network calls are made.

## Project layout

```
src/
  main.py             CLI entry point
  moltbook_client.py  HTTP client for the Moltbook API
  batch_content.py    batch file loading + sequential publishing
  verification.py     deterministic challenge solver + pluggable hook
tests/
  conftest.py         offline fakes
  test_client.py
  test_batch.py
  test_verification.py
```

## License

MIT
