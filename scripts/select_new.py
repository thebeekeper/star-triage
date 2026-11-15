"""Pick starred repos that have no note in tracker/ yet -> .triage/new.json.

    --max N            override max_per_run for this run
    --retriage LIST    also review these again: owner/name entries separated by commas
                       or spaces, or "starter" for every note that never went through the
                       pipeline (no reviewed commit). Status, source, dates and your
                       approve choice are kept; the rest of the note is rewritten.

Outputs (for GitHub Actions): count, max_turns, waiting, model, auto_merge.
"""
from __future__ import annotations

import argparse
import json
import sys

from common import (TRACKER_DIR, WORK_DIR, load_config, normalize_url, note_filename,
                    read_frontmatter, set_output, tracked_urls, write_summary)
from paths import merge_policy

KEEP_ON_RETRIAGE = ("status", "source", "added", "approve")


def retriage_items(spec: str) -> list[dict]:
    """Build batch entries for notes the user asked to review again."""
    from export_stars import get_token, repo_info

    notes = {}
    for md in sorted(TRACKER_DIR.rglob("*.md")):
        fm = read_frontmatter(md)
        if fm and fm.get("repo") and not md.name.startswith("_"):
            notes[normalize_url(str(fm["repo"]))] = (md, fm)
    words = [w.strip() for w in spec.replace(",", " ").split() if w.strip()]
    if words == ["starter"]:
        targets = [(md, fm) for md, fm in notes.values() if not fm.get("commit")]
    else:
        targets = []
        for w in words:
            hit = notes.get(normalize_url(f"github.com/{w.removeprefix('https://github.com/')}"))
            if hit:
                targets.append(hit)
            else:
                print(f"::warning::No note found for '{w}', so it was not re-reviewed.")
    token = get_token()
    items = []
    for md, fm in targets[:25]:
        full = normalize_url(str(fm["repo"])).removeprefix("github.com/")
        try:
            item = repo_info(full, token)
        except Exception as exc:  # renamed beyond redirect, deleted, or private
            print(f"::warning::Could not look up {full} ({exc}); skipped.")
            continue
        item["starred_at"] = str(fm.get("added") or "")
        item["retriage"] = True
        item["previous"] = {k: (fm[k].isoformat() if hasattr(fm[k], "isoformat") else fm[k])
                            for k in KEEP_ON_RETRIAGE if k in fm}
        item["note_path"] = md.relative_to(TRACKER_DIR.parent).as_posix()
        items.append(item)
    return items

ALLOWED_MODELS = {"sonnet", "opus", "haiku"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", default="", help="override max_per_run for this run")
    ap.add_argument("--retriage", default="", help='owner/name list, or "starter"')
    args = ap.parse_args()

    cfg = load_config()
    stars_path = WORK_DIR / "stars.json"
    if not stars_path.exists():
        sys.exit("No .triage/stars.json - run scripts/export_stars.py first.")
    stars = json.loads(stars_path.read_text(encoding="utf-8"))

    lists = []
    lists_path = WORK_DIR / "lists.json"
    if lists_path.exists():
        lists = json.loads(lists_path.read_text(encoding="utf-8"))
    membership: dict[str, list[str]] = {}
    for lst in lists:
        for full in lst.get("repos", []):
            membership.setdefault(full.lower(), []).append(lst["list"])

    again = retriage_items(args.retriage) if args.retriage.strip() else []
    ignore = {normalize_url(f"github.com/{r}") for r in cfg.get("ignore", []) or []}
    done = tracked_urls() | ignore | {normalize_url(i["url"]) for i in again}
    new = [s for s in stars if s.get("url") and not (
        normalize_url(s["url"]) in done or f"id:{s.get('id')}" in done
        or f"tracker/{note_filename(s.get('repo') or '')}" in done)]

    raw = str(args.max).strip()
    limit = int(raw) if raw.isdigit() else int(cfg["max_per_run"])
    limit = max(1, min(limit, 25))
    batch = again + new[:max(0, limit - len(again))]  # stars.json is newest-starred first
    for s in batch:
        s.setdefault("note_path", f"tracker/{note_filename(s['repo'])}")
        stem = s["note_path"].rsplit("/", 1)[-1][:-3]  # every per-repo file is keyed by the note's name
        s["lists"] = membership.get(s["repo"].lower(), [])
        s["local_path"] = f".triage/repos/{stem}"
        s["proposal_path"] = f".triage/proposals/{stem}.json"
    (WORK_DIR / "proposals").mkdir(parents=True, exist_ok=True)

    WORK_DIR.mkdir(exist_ok=True)
    (WORK_DIR / "new.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")

    model = str(cfg.get("model", "sonnet")).lower()
    if model not in ALLOWED_MODELS:
        model = "sonnet"
    set_output("count", str(len(batch)))
    set_output("max_turns", str(40 + 25 * len(batch)))
    set_output("waiting", str(max(0, len(new) - (len(batch) - len(again)))))
    set_output("model", model)
    set_output("auto_merge", merge_policy(cfg))  # the mode; publish.py decides from it
    if not batch:
        write_summary("**No new stars** since the last check. Nothing to triage, no Claude usage.")


if __name__ == "__main__":
    main()
