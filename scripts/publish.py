"""Publish this run's notes: open a review PR, or add them to the one already open.

Runs last in the workflow, after the notes are validated and plugins queued.

  - No PR open: commit the new notes on a fresh star-triage/<date> branch and open a PR.
  - A PR is still open: add the new notes to that PR as one more commit, so a week
    without merging gives you one bigger PR instead of a backlog nobody triaged.
  - mode (config.yml) decides whether the PR merges itself:
      manual     merge when it approves no new installs (default)
      auto       also merge when every newly approved install is Low risk
      auto-plus  also merge when every newly approved install is Low or Medium risk
    (An old config with `auto_merge: never` and no `mode` still always waits.)
    Merging is the approval: approved installs then happen on your computer by themselves.

Environment (set by the workflow): GH_TOKEN, GITHUB_REPOSITORY, BASE (default branch),
PR_NUMBER and PR_BRANCH (empty when no PR is open), WAITING, AUTO_MERGE (the mode, from
select_new.py; the names used before 5.2, notes and low-risk, still work).
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys

import yaml

from common import FRONTMATTER_RE, ROOT, WORK_DIR, write_summary
from paths import MODE_RISKS, may_merge

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
FOOTER = "<!-- star-triage:footer -->"
MARKET = ".claude-plugin/marketplace.json"
INSTALLS = "installs.json"


def git(*args: str, check: bool = True) -> str:
    # quotePath=false: file names with non-ASCII letters come back as they are, not escaped.
    res = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    if check and res.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{res.stderr.strip()}")
    return res.stdout.strip()


def gh(*args: str) -> subprocess.CompletedProcess:
    # STAR_TRIAGE_GH swaps in another program (tests use tests/fake_gh.py, so the real gh is never called).
    fake = os.environ.get("STAR_TRIAGE_GH")
    cmd = ([sys.executable, fake] if fake.endswith(".py") else [fake]) if fake else ["gh"]
    return subprocess.run([*cmd, *args], cwd=ROOT, capture_output=True, text=True)


def error(title: str, message: str) -> None:
    print(f"::error title={title}::{message}")
    write_summary(f"### {title}\n\n{message}")


def read(name: str) -> str:
    path = WORK_DIR / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def new_approvals(base: str) -> list[dict]:
    """Installs this PR approves that the base branch does not already approve."""
    import json
    try:
        now = json.loads((ROOT / INSTALLS).read_text(encoding="utf-8")).get("items", {})
    except (OSError, ValueError):
        return []
    try:
        before = json.loads(git("show", f"origin/{base}:{INSTALLS}", check=False) or "{}").get("items", {})
    except ValueError:
        before = {}
    def approved(item: dict, note_text: str) -> bool:
        # Same rule as sync.py: the approve box on the note wins over installs.json.
        m = FRONTMATTER_RE.match(note_text)
        try:
            tick = (yaml.safe_load(m.group(1)) or {}).get("approve") if m else None
        except (yaml.YAMLError, AttributeError):
            tick = None
        return tick if isinstance(tick, bool) else bool(item.get("approved"))

    def note_now(item: dict) -> str:
        try:
            return (ROOT / str(item.get("note", ""))).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    fresh = []
    for key, item in now.items():
        if not approved(item, note_now(item)):
            continue
        old = before.get(key) or {}
        was = bool(old) and approved(old, git("show", f"origin/{base}:{old.get('note', '')}", check=False))
        # A changed pin (commit, or the version inside spec) on an approved item is a new approval.
        if not (was and old.get("spec") == item.get("spec") and old.get("commit") == item.get("commit")):
            fresh.append(item)
    return fresh


def risk_words(policy: str) -> str:
    return " or ".join(MODE_RISKS.get(policy, ())) or "Low"


def footer(waiting: str, auto: str, notes_only: bool, will_merge: bool = False) -> str:
    lines = [FOOTER]
    if waiting == "1":
        lines.append("_1 more starred repo is waiting for the next run._")
    elif waiting not in ("", "0"):
        lines.append(f"_{waiting} more starred repos are waiting for the next run._")
    if will_merge:
        lines.append("Merged automatically: " + (f"its installs are all {risk_words(auto)} risk and the mode is {auto}."
                                                 if not notes_only else "nothing new to install."))
    elif not notes_only:
        lines.append("This PR approves installs, so it waits for you. Review the notes (especially "
                     "Install plan and Security scan), then merge. Approved items then install on your "
                     "computer by themselves the next time you open Claude Code.")
    else:
        lines.append("Review the notes, then merge.")
    return "\n\n".join(lines)


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    # The repo's default branch, from the workflow. No branch name is assumed (UT-11).
    base = os.environ.get("BASE") or git("branch", "--show-current", check=False)
    if not base:
        sys.exit("publish.py: no base branch (BASE is empty and HEAD is detached).")
    pr_number = os.environ.get("PR_NUMBER", "")
    pr_branch = os.environ.get("PR_BRANCH", "")
    waiting = os.environ.get("WAITING", "0")
    auto = os.environ.get("AUTO_MERGE") or "manual"
    auto = {"notes": "manual", "low-risk": "auto"}.get(auto, auto)
    token = os.environ["GH_TOKEN"]

    new_files = git("ls-files", "--others", "--exclude-standard", "--", "tracker", MARKET, INSTALLS).splitlines()
    edited = git("diff", "--name-only", "HEAD", "--", "tracker", MARKET, INSTALLS).splitlines()
    changed = sorted(set(new_files + edited))
    if not changed:
        print("::warning::Claude wrote no notes this run.")
        write_summary("Claude wrote no notes this run. Check the triage step's log.")
        return

    git("config", "user.name", BOT_NAME)
    git("config", "user.email", BOT_EMAIL)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        git("remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{repo}.git")

    n_new = sum(1 for p in changed if p.startswith("tracker/") and p.endswith(".md"))
    message = f"star-triage: {n_new} new repo note{'s' if n_new != 1 else ''}"
    git("add", "--", *changed)
    git("commit", "-q", "-m", message)
    work = git("rev-parse", "HEAD")

    if pr_branch:
        # Rebuild the commit on top of the open PR's branch. Only this run's notes and
        # the marketplace file change, so the push never touches workflow files.
        git("switch", "-q", "--detach", f"origin/{pr_branch}")
        git("checkout", work, "--", *changed)
        git("commit", "-q", "-m", message)
        branch = pr_branch
    else:
        branch = "star-triage/" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        git("switch", "-q", "-c", branch)
    git("push", "-q", "origin", f"HEAD:refs/heads/{branch}")

    pr_files = git("diff", "--name-only", f"origin/{base}...HEAD").splitlines()
    notes_in_pr = [p for p in pr_files if p.startswith("tracker/") and p.endswith(".md")]
    approvals = new_approvals(base)
    notes_only = not approvals
    can_merge = may_merge(auto, [a.get("risk") for a in approvals])
    title = f"star-triage: {len(notes_in_pr)} new repo{'s' if len(notes_in_pr) != 1 else ''}"
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    section = f"### Run {today}\n\n" + (read("summary.md") or f"{n_new} new notes in tracker/.")
    if read("install-summary.md"):
        section += "\n\n" + read("install-summary.md")

    if pr_number:
        old = gh("pr", "view", pr_number, "--repo", repo, "--json", "body", "--jq", ".body").stdout
        old = old.split(FOOTER)[0].rstrip()
        body = f"{old}\n\n{section}\n\n{footer(waiting, auto, notes_only, can_merge)}\n"
    else:
        body = f"{section}\n\n{footer(waiting, auto, notes_only, can_merge)}\n"
    body_path = WORK_DIR / "pr-body.md"
    body_path.write_text(body, encoding="utf-8")

    if pr_number:
        res = gh("pr", "edit", pr_number, "--repo", repo, "--title", title, "--body-file", str(body_path))
        if res.returncode != 0:
            print(f"::warning::Notes were added to PR #{pr_number}, but its description was not updated: "
                  f"{res.stderr.strip()}")
        url = f"https://github.com/{repo}/pull/{pr_number}"
        verb = f"Added {n_new} note{'s' if n_new != 1 else ''} to the open review PR"
    else:
        res = gh("pr", "create", "--repo", repo, "--base", base, "--head", branch,
                 "--title", title, "--body-file", str(body_path))
        if res.returncode != 0:
            git("push", "-q", "origin", "--delete", branch, check=False)
            if "not permitted to create or approve pull requests" in res.stderr:
                error("GitHub Actions is not allowed to open pull requests",
                      f"Turn it on: https://github.com/{repo}/settings/actions , then under "
                      "**Workflow permissions** tick **Allow GitHub Actions to create and approve pull "
                      "requests** and click Save. Or run `python star-triage.py setup`, which does it for you. "
                      "Then run the workflow again.")
            else:
                error("Could not open the review PR", res.stderr.strip())
            sys.exit(1)
        url = res.stdout.strip().splitlines()[-1]
        pr_number = url.rstrip("/").rsplit("/", 1)[-1]
        verb = "Opened a review PR"

    if can_merge:
        merged = None
        head = git("rev-parse", "HEAD")  # merge only the commit this run checked, nothing pushed since
        for method in ("--squash", "--merge", "--rebase"):
            res = gh("pr", "merge", pr_number, "--repo", repo, method, "--delete-branch",
                     "--match-head-commit", head)
            if res.returncode == 0:
                merged = True
                break
            merged = res.stderr.strip()
        if merged is True:
            what = (f"{len(approvals)} {risk_words(auto)}-risk install(s) approved, mode {auto}"
                    if approvals else "nothing new to install")
            write_summary(f"{verb} and **merged it automatically** ({what}): {url}\n\n{section}")
            return
        print(f"::warning::Could not auto-merge PR #{pr_number}: {merged}")
        write_summary(f"{verb}: {url}\n\nIt could merge itself, but auto-merge failed ({merged}). "
                      "Merge it yourself, or check branch protection rules.\n\n" + section)
        return

    if auto == "never":
        why = "auto_merge is set to never"
    elif auto in ("auto", "auto-plus"):
        why = f"it approves installs that are not {risk_words(auto)} risk (mode {auto})"
    else:
        why = f"it approves {len(approvals)} install(s)"
    write_summary(f"{verb}: {url}\n\n**Waiting for you to merge**, because {why}.\n\n{section}")


if __name__ == "__main__":
    main()
