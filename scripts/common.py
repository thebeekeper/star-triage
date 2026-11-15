"""Shared helpers for star-triage scripts. Standard library + PyYAML only."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

from paths import DEFAULT_MAX_PER_RUN, find_data_root  # noqa: E402  (standard library only)

ROOT = find_data_root() or Path.cwd()  # your data repo (see paths.py)
CONFIG_PATH = ROOT / "config.yml"
TRACKER_DIR = ROOT / "tracker"
WORK_DIR = ROOT / ".triage"  # git-ignored scratch space
# In CI, the run copies new.json and the scan results here after the scan and
# before Claude runs. Steps that make decisions read from this copy, so nothing
# Claude writes in the workspace can change what gets approved.
TRUSTED_DIR = Path(os.environ.get("STAR_TRIAGE_TRUSTED") or WORK_DIR)
RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        sys.exit(f"config file not found: {path}")
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("github_user", "")
    cfg.setdefault("max_per_run", DEFAULT_MAX_PER_RUN)
    cfg.setdefault("model", "sonnet")
    cfg.setdefault("contexts", [])
    cfg.setdefault("labels", {})
    return cfg


def github_user(cfg: dict) -> str:
    user = (cfg.get("github_user") or "").strip()
    if user:
        return user
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    if owner:
        return owner
    sys.exit("No GitHub user: set github_user in config.yml (or run inside GitHub Actions).")


def normalize_url(url: str) -> str:
    url = (url or "").strip().lower()
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = re.sub(r"\.git$", "", url)
    return url.rstrip("/")


def read_frontmatter(path: Path) -> dict | None:
    """Return the YAML frontmatter of a markdown file, or None if it has none."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def split_note(path: Path) -> tuple[dict, str]:
    """Return (frontmatter, body) of a note."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:  # validate_notes rejects the note later
        return {}, text
    return (fm if isinstance(fm, dict) else {}), text[m.end():]


def _yaml_value(v) -> str:
    import datetime as _dt
    import json as _json
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_json.dumps(str(x), ensure_ascii=False) for x in v) + "]"
    return _json.dumps("" if v is None else str(v), ensure_ascii=False)


def write_note(path: Path, fm: dict, body: str) -> None:
    lines = ["---"] + [f"{k}: {_yaml_value(v)}" for k, v in fm.items()] + ["---"]
    text = "\n".join(lines) + "\n"
    if body.strip():
        text += "\n" + body.lstrip("\n")
    path.write_text(text, encoding="utf-8")


def tracked_urls(tracker_dir: Path = TRACKER_DIR) -> set[str]:
    """Keys of every repo that already has a note in tracker/: its URL, "id:<repo_id>" and
    the note's own path ("tracker/owner__name.md", even if its front matter is unreadable)."""
    urls = set()
    if not tracker_dir.exists():
        return urls
    for md in tracker_dir.rglob("*.md"):
        urls.add(md.relative_to(tracker_dir.parent).as_posix())
        fm = read_frontmatter(md)
        if fm and fm.get("repo"):
            urls.add(normalize_url(str(fm["repo"])))
        if fm and fm.get("repo_id"):
            urls.add(f"id:{fm['repo_id']}")
    return urls


def note_filename(full_name: str) -> str:
    """owner/name -> owner__name.md (safe on Windows, macOS and Linux)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", full_name.replace("/", "__"))
    return f"{safe}.md"


def write_summary(markdown: str) -> None:
    """Append to the run's summary page in GitHub Actions; print otherwise."""
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(markdown.rstrip() + "\n\n")
    else:
        print(markdown)


def set_output(name: str, value: str) -> None:
    """Write a step output when running in GitHub Actions; print otherwise."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"{name}={value}")
