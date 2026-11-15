"""Export the user's GitHub stars (and public star lists, best effort) to .triage/.

Token: GITHUB_TOKEN or GH_TOKEN env var, else `gh auth token`. Stars are public
data, so the default Actions token is enough.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

from common import WORK_DIR, github_user, load_config

API = "https://api.github.com"


def get_token() -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    if os.environ.get("STAR_TRIAGE_NO_GH") == "1":
        return None  # tests: never borrow the real gh login
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                              check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def request(url: str, token: str | None, accept: str, body: bytes | None = None):
    headers = {"Accept": accept, "User-Agent": "star-triage",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp), resp.headers.get("Link", "")


def next_link(link_header: str) -> str | None:
    m = re.search(r'<([^>]+)>;\s*rel="next"', link_header or "")
    return m.group(1) if m else None


def repo_info(full_name: str, token: str | None) -> dict:
    """Current metadata for one repo, in the same shape as a star. Follows renames."""
    repo, _ = request(f"{API}/repos/{full_name}", token, "application/vnd.github+json")
    lic = repo.get("license") or {}
    return {"repo": repo.get("full_name"), "id": repo.get("id"), "url": repo.get("html_url"),
            "description": repo.get("description"), "language": repo.get("language"),
            "topics": repo.get("topics") or [], "license": lic.get("spdx_id"),
            "stars": repo.get("stargazers_count"), "archived": repo.get("archived"),
            "fork": repo.get("fork"), "pushed_at": repo.get("pushed_at"), "starred_at": None}


def export_stars(user: str, token: str | None) -> list[dict]:
    url = f"{API}/users/{user}/starred?per_page=100"
    stars = []
    while url:
        page, link = request(url, token, "application/vnd.github.star+json")
        for item in page:
            repo = item.get("repo", {})
            lic = repo.get("license") or {}
            stars.append({
                "repo": repo.get("full_name"),
                "id": repo.get("id"),
                "url": repo.get("html_url"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "topics": repo.get("topics") or [],
                "license": lic.get("spdx_id"),
                "stars": repo.get("stargazers_count"),
                "archived": repo.get("archived"),
                "fork": repo.get("fork"),
                "pushed_at": repo.get("pushed_at"),
                "starred_at": item.get("starred_at"),
            })
        url = next_link(link)
    stars.sort(key=lambda s: s.get("starred_at") or "", reverse=True)
    return stars


LISTS_QUERY = """
query($login: String!) {
  user(login: $login) {
    lists(first: 50) {
      nodes { name items(first: 100) { nodes { ... on Repository { nameWithOwner } } } }
    }
  }
}"""


def export_lists(user: str, token: str | None) -> list[dict] | None:
    if not token:
        return None
    body = json.dumps({"query": LISTS_QUERY, "variables": {"login": user}}).encode()
    try:
        data, _ = request(f"{API}/graphql", token, "application/json", body)
        nodes = data["data"]["user"]["lists"]["nodes"]
    except (urllib.error.URLError, KeyError, TypeError, ValueError):
        return None
    return [{"list": n["name"],
             "repos": [i["nameWithOwner"] for i in n["items"]["nodes"] if i]}
            for n in nodes]


def main() -> None:
    cfg = load_config()
    user = github_user(cfg)
    token = get_token()
    WORK_DIR.mkdir(exist_ok=True)
    try:
        stars = export_stars(user, token)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub API error {e.code} while reading stars for '{user}': {e.reason}")
    (WORK_DIR / "stars.json").write_text(json.dumps(stars, indent=2), encoding="utf-8")
    print(f"Exported {len(stars)} stars for {user}")

    lists = export_lists(user, token)
    if lists is not None:
        (WORK_DIR / "lists.json").write_text(json.dumps(lists, indent=2), encoding="utf-8")
        print(f"Exported {len(lists)} star lists")
    else:
        print("Star lists not available from the API (skipped)")


if __name__ == "__main__":
    main()
