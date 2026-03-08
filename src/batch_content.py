"""
Batch content publishing for Moltbook agents.
Allows scheduling and publishing multiple posts efficiently.
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from moltbook_client import MoltbookClient, MoltbookError


class BatchContent:
    """Manages batch posting operations"""
    
    def __init__(self, client: MoltbookClient):
        self.client = client
        self.results = []
    
    def load_batch_file(self, filepath: str) -> List[Dict]:
        """Load batch content from JSON file"""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Batch file not found: {filepath}")
        
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        # Handle both a list of posts or a dict with "posts" key
        if isinstance(data, dict) and "posts" in data:
            posts = data["posts"]
        elif isinstance(data, list):
            posts = data
        else:
            raise ValueError("Batch file must contain a list of posts or {\"posts\": [...]}")
        
        return posts
    
    def validate_posts(self, posts: List[Dict]) -> tuple[bool, List[str]]:
        """Validate all posts have required fields"""
        errors = []
        for i, post in enumerate(posts):
            if "title" not in post:
                errors.append(f"Post {i}: missing 'title'")
            if "content" not in post:
                errors.append(f"Post {i}: missing 'content'")
            if not isinstance(post.get("title"), str) or not post["title"].strip():
                errors.append(f"Post {i}: title must be non-empty string")
            if not isinstance(post.get("content"), str) or not post["content"].strip():
                errors.append(f"Post {i}: content must be non-empty string")
        
        return len(errors) == 0, errors
    
    def publish_batch(self, posts: List[Dict], delay_seconds: float = 0, dry_run: bool = False) -> List[Dict]:
        """
        Publish multiple posts with optional delays between them
        
        Args:
            posts: List of post dicts with 'title', 'content', and optional 'submolt'
            delay_seconds: Delay between posts (useful to avoid rate limiting)
            dry_run: If True, only show what would be posted without actually posting
        
        Returns:
            List of results for each post
        """
        self.results = []
        
        # Validate before publishing
        valid, errors = self.validate_posts(posts)
        if not valid:
            for error in errors:
                self.results.append({
                    "status": "error",
                    "message": error
                })
            return self.results
        
        for i, post in enumerate(posts):
            title = post["title"]
            content = post["content"]
            submolt = post.get("submolt", "general")
            
            if dry_run:
                self.results.append({
                    "status": "dry-run",
                    "index": i,
                    "submolt": submolt,
                    "title": title,
                    "message": "Would publish to " + submolt
                })
                print(f"\n[{i+1}/{len(posts)}] DRY RUN: {title}")
            else:
                try:
                    print(f"\n[{i+1}/{len(posts)}] Publishing: {title}...", end=" ", flush=True)
                    resp = self.client.create_post(submolt, title, content)
                    
                    post_id = resp.get("post", {}).get("id")
                    self.results.append({
                        "status": "success",
                        "index": i,
                        "title": title,
                        "post_id": post_id,
                        "submolt": submolt
                    })
                    print("✓ Posted")
                    
                except MoltbookError as e:
                    self.results.append({
                        "status": "error",
                        "index": i,
                        "title": title,
                        "error": str(e)
                    })
                    print(f"✗ Error: {e}")
            
            # Delay between posts (except after the last one)
            if delay_seconds > 0 and i < len(posts) - 1:
                time.sleep(delay_seconds)
        
        return self.results
    
    def print_summary(self):
        """Print summary of batch operation"""
        if not self.results:
            return
        
        successful = sum(1 for r in self.results if r.get("status") == "success")
        errors = sum(1 for r in self.results if r.get("status") == "error")
        dry_runs = sum(1 for r in self.results if r.get("status") == "dry-run")
        
        print("\n" + "="*60)
        print("BATCH OPERATION SUMMARY")
        print("="*60)
        
        if dry_runs > 0:
            print(f"Dry runs (would publish):    {dry_runs}")
        if successful > 0:
            print(f"Successfully published:      {successful}")
        if errors > 0:
            print(f"Errors:                      {errors}")
        
        if successful > 0:
            print(f"\nPublished {successful} post(s) with {successful} upvote(s) each")
        
        print("="*60)


def create_sample_batch_file(filepath: str = "batch_sample.json"):
    """Create a sample batch content file"""
    sample = {
        "posts": [
            {
                "submolt": "general",
                "title": "My First Batch Post",
                "content": "This is the first post in a batch. It's a great way to share multiple thoughts at once!"
            },
            {
                "submolt": "general",
                "title": "Second Batch Post",
                "content": "Batch posting lets you publish multiple posts efficiently. Each post goes through the same flow but automatically."
            },
            {
                "submolt": "general",
                "title": "Building Community Through Automation",
                "content": "Automated posting is perfect for sharing research, updates, or regular content. You can schedule these batches on a cron job for continuous engagement!"
            }
        ]
    }
    
    with open(filepath, 'w') as f:
        json.dump(sample, f, indent=2, ensure_ascii=False)
    
    print(f"Sample batch file created: {filepath}")
    print(f"Edit this file to customize your posts, then run:")
    print(f"  python src/main.py batch-publish --file {filepath}")
