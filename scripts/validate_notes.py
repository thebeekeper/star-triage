"""Check Claude's output before anything is committed.

--changed (CI): every change must be a NEW file listed in .triage/new.json under
tracker/, or an edit to a note this run was asked to re-review. Any other edit,
deletion or new file, and any note that fails the checks, is undone with a
::warning:: (a copy goes to .triage/rejected/) and the valid notes go on. The run
fails only if repos were expected and no valid note is left.
--all: validate the frontmatter of every note in tracker/ (useful locally).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import re

from common import RISK_ORDER, ROOT, TRACKER_DIR, TRUSTED_DIR, WORK_DIR, load_config, read_frontmatter

REQUIRED = ["type", "category", "status", "verdict", "risk", "saves_tokens",
            "relevant_to", "repo", "summary", "install", "licence", "source",
            "added", "reviewed"]
SINGLE = ["type", "status", "verdict", "risk", "saves_tokens", "source"]
MULTI = ["category", "relevant_to"]
SECTIONS = ["## What it is", "## Verdict"]
# eligible and not-recommended are from older versions and still accepted.
AUTO_INSTALL = (None, "queued", "available", "blocked", "manual", "none", "eligible", "not-recommended")
INSTALL_KINDS = (None, "plugin", "skill", "mcp", "cli", "manual", "none")


def as_text(v):
    if isinstance(v, bool):  # YAML 1.1 reads bare Yes/No as booleans
        return "Yes" if v else "No"
    return str(v)


def allowed_values(cfg: dict) -> dict[str, set[str]]:
    labels = {k: {as_text(x) for x in (v or [])} for k, v in cfg["labels"].items()}
    labels["relevant_to"] = {as_text(c["name"]) for c in cfg["contexts"]}
    return labels


def check_note(path: Path, allowed: dict[str, set[str]]) -> list[str]:
    errs = []
    fm = read_frontmatter(path)
    if fm is None:
        return [f"{path}: missing or invalid YAML frontmatter"]
    for key in REQUIRED:
        if key not in fm or fm[key] in (None, ""):
            errs.append(f"{path}: missing '{key}'")
    for key in SINGLE:
        if key in fm and key in allowed and allowed[key] and as_text(fm[key]) not in allowed[key]:
            errs.append(f"{path}: {key}='{fm[key]}' not in {sorted(allowed[key])}")
    for key in MULTI:
        vals = fm.get(key)
        if vals is None:
            continue
        if not isinstance(vals, list):
            errs.append(f"{path}: '{key}' must be a list")
            continue
        bad = [v for v in vals if allowed.get(key) and as_text(v) not in allowed[key]]
        if bad:
            errs.append(f"{path}: {key} has unknown values {bad}")
    repo = str(fm.get("repo", ""))
    if repo and not repo.startswith("https://github.com/"):
        errs.append(f"{path}: repo must be a https://github.com/ URL")
    for key in ("added", "reviewed"):
        if key in fm and not isinstance(fm[key], (dt.date, dt.datetime)):
            try:
                dt.date.fromisoformat(str(fm[key]))
            except ValueError:
                errs.append(f"{path}: {key} must be YYYY-MM-DD")
    if fm.get("scan_risk") is not None:
        floor, risk = as_text(fm["scan_risk"]), as_text(fm.get("risk", ""))
        if RISK_ORDER.get(risk, -1) < RISK_ORDER.get(floor, 0):
            errs.append(f"{path}: risk '{risk}' is lower than the security scan's floor '{floor}'")
    if fm.get("commit") and not re.fullmatch(r"[0-9a-f]{40}", str(fm["commit"])):
        errs.append(f"{path}: commit must be a full 40-character SHA")
    if fm.get("auto_install") not in AUTO_INSTALL:
        errs.append(f"{path}: unknown auto_install value '{fm.get('auto_install')}'")
    if fm.get("install_kind") not in INSTALL_KINDS:
        errs.append(f"{path}: unknown install_kind '{fm.get('install_kind')}'")
    if "approve" in fm and not isinstance(fm["approve"], bool):
        errs.append(f"{path}: approve must be true or false (a checkbox in Obsidian)")
    body = path.read_text(encoding="utf-8")
    for s in SECTIONS:
        if s not in body:
            errs.append(f"{path}: missing section '{s}'")
    return errs


def changed_paths() -> list[tuple[str, str]]:
    out = subprocess.run(["git", "-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=all"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return [(line[:2], line[3:].strip().strip('"')) for line in out.splitlines() if line]


def reject(rel: str, status: str, reasons: list[str]) -> None:
    """Take a bad note out of this run: keep a copy in .triage/rejected/ and put tracker/
    back the way it was (a new file is removed, an existing one is restored from HEAD)."""
    path = ROOT / rel
    if path.is_file():
        dest = WORK_DIR / "rejected"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest / path.name)
        if status == "??":
            path.unlink()
    if status != "??":
        subprocess.run(["git", "checkout", "-q", "HEAD", "--", rel], cwd=ROOT, check=False)
    print(f"::warning title=Note rejected::{rel}: " + "; ".join(reasons).replace("\n", " "))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    allowed = allowed_values(cfg)
    errors: list[str] = []
    notes: list[Path] = []
    rejected = 0

    if args.changed:
        batch = json.loads((TRUSTED_DIR / "new.json").read_text(encoding="utf-8"))
        expected = {i["note_path"] for i in batch if not i.get("retriage")}
        rewritable = {i["note_path"] for i in batch if i.get("retriage")}
        for status, rel in changed_paths():
            if not rel.startswith("tracker/"):
                # Never committed (the PR step only adds tracker/), but worth knowing about.
                print(f"warning: ignoring change outside tracker/: {rel}")
                continue
            if rel in rewritable and status.strip() == "M":
                why = check_note(ROOT / rel, allowed)
            elif status != "??":
                why = [f"existing file was modified or deleted ({status.strip()})"]
            elif rel not in expected:
                why = ["unexpected new file (only notes for this run's repos are allowed)"]
            else:
                why = check_note(ROOT / rel, allowed)
            if why:
                reject(rel, status, why)
                rejected += 1
            else:
                notes.append(ROOT / rel)
        # Bad notes are set aside and the good ones go on. Fail only when nothing is left.
        if batch and not notes:
            sys.exit(f"Validation failed: no valid note out of {len(batch)} expected ({rejected} rejected).")
        print(f"OK: {len(notes)} note(s) valid, {rejected} rejected (copies in .triage/rejected/)")
        return
    if args.all:
        notes = [p for p in TRACKER_DIR.rglob("*.md")
                 if not p.name.startswith("_") and (read_frontmatter(p) or {}).get("repo")]

    for note in notes:
        errors += check_note(note, allowed)

    if errors:
        print("Validation failed:")
        print("\n".join(f"  - {e}" for e in errors))
        sys.exit(1)
    print(f"OK: {len(notes)} note(s) valid")


if __name__ == "__main__":
    main()
