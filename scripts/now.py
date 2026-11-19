"""Review the stars you just added and install what is safe, now, in one go.

    python star-triage.py now                        start a review run on GitHub, wait, then install
    python star-triage.py now --max 5                review at most 5 repos in this run
    python star-triage.py now --retriage a/b,c/d     review these repos again
    python star-triage.py now --merge 7 --head SHA   (after you said yes) merge review PR #7, then install
    python star-triage.py now --no-merge 7           (after you said no) leave PR #7 open, then install

In order: start the star-triage workflow on GitHub (or wait for the run already going), wait
for it, and, when its review PR waits for you, ask before merging it. Then pull and install
what is approved, as `sync --now` does.

The review still happens on GitHub: the security scan, then Claude, in the workflow. `now` only
skips the waiting (the schedule, and the safety delay of the auto modes). The daily limit,
pause and the High-risk and blocked rules all stay; a blocked item still needs
`python star-triage.py sync` in your own terminal and a typed "install". It never merges a
review PR without your yes. Needs the GitHub CLI (gh), logged in. Only the Python standard
library is needed.

Exit codes: 0 done, 1 something stopped it (the message says the fix), 3 the review PR waits
for your answer and this was not run in a terminal (the two commands to answer are printed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402  (gh helpers; STAR_TRIAGE_GH lets the tests use a fake gh)
import reviews  # noqa: E402
import sync  # noqa: E402  (finds the data repo, see paths.py)
from paths import config_on, read_simple_config, safe_console  # noqa: E402

ROOT = sync.ROOT
WORKFLOW = doctor.WORKFLOW_FILE
FIND_FOR, FIND_EVERY = 60, 3          # a started run can take a few seconds to show up
WAIT_FOR, POLL, SAY_EVERY = 20 * 60, 10, 20
ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")
BOT_LOGINS = ("app/github-actions", "github-actions")
RUN_FIELDS = "databaseId,status,conclusion,url,createdAt,event"
PR_FIELDS = "number,url,state,title,headRefName,headRefOid,isCrossRepository,author,updatedAt,body"
WAITS_HEADING = "### Installs automatically once merged"
SHOW_AT_MOST = 10

clock, sleep = time.monotonic, time.sleep  # the tests swap these for a fake clock


def gh(*args: str) -> subprocess.CompletedProcess:
    return doctor.run(["gh", *args])


def when(text) -> dt.datetime | None:
    try:
        stamp = dt.datetime.fromisoformat(str(text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone.utc)


def minsec(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def launcher(command: str) -> str:
    return f"python star-triage.py {command}"


# ----------------------------------------------------------------- 1. start a run

def preflight() -> str | None:
    """owner/name of the data repo on GitHub, or None after printing the one fix."""
    if not doctor.gh_installed():
        print("The GitHub CLI (gh) is not installed. Install it from https://cli.github.com , then run: gh auth login")
        return None
    if not doctor.gh_ready():
        print("The GitHub CLI is not logged in. Run: gh auth login")
        return None
    repo = doctor.detect_repo(ROOT)
    if not repo:
        print(f"{ROOT} has no GitHub remote (origin), so there is no workflow to start. Run: {launcher('setup')}")
        return None
    if doctor.workflow_registered(repo) is False:
        print("GitHub Actions hasn't registered the star-triage workflow, so it can't be started. "
              f"Fix: {launcher('setup --repair')}")
        return None
    return repo


def runs(repo: str, event: str | None = None) -> list[dict]:
    args = ["run", "list", "--repo", repo, "--workflow", WORKFLOW, "--limit", "10", "--json", RUN_FIELDS]
    data = doctor.gh_json(*args, *(["--event", event] if event else []))
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def start_run(repo: str, most: int | None, retriage: str | None) -> dict | None:
    """The run to wait for: one already going (the concurrency group would queue a second one
    behind it anyway), or a new workflow_dispatch run started here."""
    listed = runs(repo)
    going = [r for r in listed if r.get("status") in ACTIVE]
    if going:
        print("A star-triage run is already going, so this waits for it instead of starting a second one.")
        if most or retriage:
            print("(--max and --retriage apply only to a run started here; that run keeps its own settings.)")
        return going[0]  # newest first: a queued run finishes after the one in progress
    before = {r.get("databaseId") for r in listed}
    started = dt.datetime.now(dt.timezone.utc)
    cmd = ["workflow", "run", WORKFLOW, "--repo", repo]
    if most:
        cmd += ["-f", f"max={most}"]
    if retriage:
        cmd += ["-f", f"retriage={retriage}"]
    res = gh(*cmd)
    if res.returncode:
        err = (res.stderr or res.stdout or "").strip()
        if "404" in err or "not found" in err.lower():
            print("GitHub couldn't find the star-triage workflow on your default branch. "
                  f"Fix: {launcher('setup --repair')}")
        else:
            print(f"Couldn't start a run: {err.splitlines()[-1] if err else 'gh workflow run failed'}")
            print(doctor.actions_hint(repo))
        return None
    print("Started a review run on GitHub. Looking for it...", flush=True)
    deadline = clock() + FIND_FOR
    while True:
        new = [r for r in runs(repo, "workflow_dispatch") if r.get("databaseId") not in before
               and (when(r.get("createdAt")) or started) >= started - dt.timedelta(minutes=2)]
        if new:
            return new[0]
        if clock() >= deadline:
            break
        sleep(FIND_EVERY)
    print(f"The run was started, but it hasn't shown up after {FIND_FOR} s. Follow it at "
          f"https://github.com/{repo}/actions , and when it has finished run: {launcher('sync --now')}")
    return None


# ----------------------------------------------------------------- 2. wait for it

def wait(repo: str, run: dict) -> dict | None:
    """The finished run, or None after WAIT_FOR (the URL is printed)."""
    rid, url = str(run.get("databaseId")), run.get("url") or f"https://github.com/{repo}/actions"
    print(f"Run: {url}", flush=True)
    began = said = clock()
    while True:
        info = doctor.gh_json("run", "view", rid, "--repo", repo, "--json", "status,conclusion,url,createdAt")
        if isinstance(info, dict):
            run = {**run, **{k: v for k, v in info.items() if v not in (None, "")}}
        if run.get("status") == "completed":
            return run
        if clock() - began >= WAIT_FOR:
            print(f"Still running after {WAIT_FOR // 60} minutes, so this stopped waiting. Follow it here: {url}\n"
                  f"When it has finished, install what it approved with: {launcher('sync --now')}")
            return None
        if clock() - said >= SAY_EVERY:
            print(f"reviewing... {minsec(clock() - began)}", flush=True)
            said = clock()
        sleep(POLL)


def failure_reason(repo: str, rid: str) -> list[str]:
    """The ANNOTATIONS part of `gh run view` (the workflow's ::error lines), without locations."""
    lines = gh("run", "view", rid, "--repo", repo).stdout.splitlines()
    try:
        start = [line.strip() for line in lines].index("ANNOTATIONS")
    except ValueError:
        return []
    out = []
    for line in lines[start + 1:]:
        text = line.strip()
        if text.startswith(("To see what failed", "View this run", "For more information")):
            break
        if text and not re.fullmatch(r"\S+: \S+#\d+", text):
            out.append(text)
    return out[:6]


# ----------------------------------------------------------------- 3. the review PR

def ours(pr: dict) -> bool:
    """A review PR this workflow opened: a star-triage/ branch in this repo, by github-actions."""
    return (str(pr.get("headRefName", "")).startswith("star-triage/") and not pr.get("isCrossRepository")
            and (pr.get("author") or {}).get("login") in BOT_LOGINS)


def review_pr(repo: str, since: dt.datetime) -> dict | None:
    """The review PR this run opened or added to (it was updated after the run was created)."""
    data = doctor.gh_json("pr", "list", "--repo", repo, "--state", "all", "--limit", "10", "--json", PR_FIELDS)
    for pr in data if isinstance(data, list) else []:
        if isinstance(pr, dict) and ours(pr) and (when(pr.get("updatedAt")) or since) >= since:
            return pr
    return None


def pr_count(pr: dict) -> int:
    """Repos in the review PR, from its title ('star-triage: 3 new repos'); 0 when it doesn't say."""
    m = re.search(r"(\d+) new repo", str(pr.get("title", "")))
    return int(m.group(1)) if m else 0


def pr_approves(pr: dict) -> list[str]:
    """What merging the PR approves: the bullets under each run's 'Installs automatically once merged'."""
    out, inside = [], False
    for line in str(pr.get("body") or "").splitlines():
        if line.startswith("### ") or line.startswith("## "):
            inside = line.strip() == WAITS_HEADING
        elif inside and line.startswith("- "):
            out.append(line[2:].strip())
    return list(dict.fromkeys(out))


def pr_wait_line(pr: dict) -> str:
    names = [a.split(":", 1)[0] for a in pr_approves(pr)]
    return (f"review PR #{pr.get('number')} ({pr.get('url')}): merge it to approve "
            + (", ".join(names) if names else "its installs"))


def ask_merge() -> bool | None:
    """True/False from the terminal; None when there is no terminal to ask in."""
    if not sys.stdin or not sys.stdin.isatty():
        return None
    try:
        return input("Merge it now? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def merge(repo: str, number: int, head: str | None) -> bool:
    """Merge a review PR the user said yes to, only if it is still the commit they saw."""
    pr = doctor.gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    if not isinstance(pr, dict) or not ours(pr):
        print(f"PR #{number} is not a star-triage review PR, so it was not merged.")
        return False
    url, state, sha = pr.get("url"), str(pr.get("state", "")).upper(), str(pr.get("headRefOid") or "")
    if state != "OPEN":
        print(f"Review PR #{number} is {state.lower() or 'not open'} already: {url}")
        return state == "MERGED"
    if head and not sha.startswith(head):
        print(f"Review PR #{number} changed since you saw it, so it was not merged. Look again: {url}")
        return False
    err = ""
    for method in ("--squash", "--merge", "--rebase"):
        res = gh("pr", "merge", str(number), "--repo", repo, method, "--delete-branch", "--match-head-commit", sha)
        if res.returncode == 0:
            print(f"Merged review PR #{number}.")
            return True
        err = ((res.stderr or res.stdout or "").strip().splitlines() or [""])[-1]
    print(f"Couldn't merge review PR #{number} ({err or 'gh pr merge failed'}). Merge it on GitHub: {url}")
    return False


# ----------------------------------------------------------------- 4-6. pull, install, summary

def git_head() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def changed_notes(old: str, new: str) -> set[str]:
    if not old or not new or old == new:
        return set()
    try:
        res = subprocess.run(["git", "diff", "--name-only", old, new, "--", "tracker"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {p for p in res.stdout.splitlines() if p.endswith(".md")}


def waiting_for_you(mode: str, fresh: set[str]) -> list[str]:
    """One line per review that needs you: High risk, blocked, not ticked; and, for this run's
    reviews, a Skip verdict or a manual install."""
    notes = reviews.load_notes()
    status = reviews.statuses(notes)
    approved = sync.wanted(sync.load_json(sync.INSTALLS_PATH, {}).get("items", {}))
    obsidian = config_on(read_simple_config(ROOT / "config.yml"), "obsidian")
    lines = []
    for n in notes:
        fm, item, key = n["fm"], n["item"] or {}, n["key"]
        repo = fm.get("repo") or key
        if status[n["note"]] == "blocked":
            why = (f"blocked by the security scan ({item.get('blocked') or 'see its note'}); to install it "
                   f"anyway, run {launcher('sync')} in your own terminal and type install")
        elif status[n["note"]] == "waiting for you":
            if key in approved and item.get("risk") == "High":
                why = f"High risk, never installs by itself; to install it, run {launcher('sync')} yourself"
            else:
                how = "tick `approve` on the note" if obsidian else f"run {launcher('approve ' + str(key))}"
                why = (f"not ticked (verdict {fm.get('verdict') or '?'}, risk {fm.get('risk') or item.get('risk') or '?'}); "
                       f"to install it, {how}")
        elif n["note"] in fresh and str(fm.get("verdict", "")).lower() == "skip":
            why = "verdict Skip"
        elif n["note"] in fresh and (fm.get("auto_install") == "manual" or item.get("status") == "manual"):
            why = "manual install: see the Install section of its note"
        else:
            continue
        lines.append(f"{repo} ({n['note']}): {why}")
    return lines


def finish(reviewed: int | None, pr_wait: str | None) -> int:
    old = git_head()
    print("\nPulling the latest reviews...", flush=True)
    if sync.pull(quiet=True) is False:
        print("Couldn't pull from GitHub (git pull --ff-only failed), so this uses what is on this computer.")
    fresh = changed_notes(old, git_head())
    mode = sync.settings()[0]
    if mode == "manual":
        print("Your star-triage mode is manual: only items you ticked `approve` on, and Install verdicts the "
              "review approved (in a review PR that was merged), install. Nothing else installs by itself.")
    print("Installing what is approved, without the safety delay (the daily limit, pause and the High-risk "
          "and blocked rules still apply)...", flush=True)
    out: dict = {}
    code = sync.install_now(False, True, sync.read_scope(), out)
    result = out.get("result") or {}

    print("\nSummary")
    if reviewed is None:
        print("  No new stars were reviewed.")
    else:
        print(f"  Reviewed {reviewed} repo{'s' if reviewed != 1 else ''}." if reviewed else "  Reviewed the new stars.")
    names = [r.get("name") for r in result.get("log", []) if r.get("action") in ("installed", "updated")]
    if out.get("paused"):
        print("  Installed: nothing, star-triage is paused on this computer (resume: python star-triage.py resume).")
    else:
        print(f"  Installed: {', '.join(names) if names else 'nothing new'}.")
    capped = [sync.short_name(k, i) for _, k, i in out.get("capped", [])]
    if capped:
        print(f"  Tomorrow (daily limit {out.get('limit')}): {', '.join(capped)}.")
    waiting = ([pr_wait] if pr_wait else []) + waiting_for_you(mode, fresh)
    if waiting:
        print("  Waiting for you:" + ("" if mode != "manual" else " (manual mode: these wait for a tick or your merge)"))
        for line in waiting[:SHOW_AT_MOST]:
            print(f"    - {line}")
        if len(waiting) > SHOW_AT_MOST:
            print(f"    ... and {len(waiting) - SHOW_AT_MOST} more: {launcher('list --waiting')}")
    else:
        print("  Waiting for you: nothing.")
    if result.get("done") and sync.needs_restart(result):
        print("Restart Claude Code to load new plugins and MCP servers.")
    return code


# ----------------------------------------------------------------- main

def answer_given(repo: str, args) -> int:
    """`--merge N` (the user said yes) or `--no-merge N` (no), after an earlier `now` asked."""
    number = args.merge or args.no_merge
    pr = doctor.gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    pr = pr if isinstance(pr, dict) else {"number": number, "url": f"https://github.com/{repo}/pull/{number}"}
    if args.merge:
        merged = merge(repo, number, args.head)
        return finish(pr_count(pr), None if merged else pr_wait_line(pr))
    print(f"Left review PR #{number} open: {pr.get('url')}")
    return finish(pr_count(pr), pr_wait_line(pr))


def main(argv: list[str] | None = None) -> int:
    safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 prog="star-triage.py now")
    ap.add_argument("--max", type=int, metavar="N", help="review at most N repos in this run")
    ap.add_argument("--retriage", metavar="LIST", help="review these again: owner/name,owner/name (or: starter)")
    ap.add_argument("--merge", type=int, metavar="PR", help="the user said yes: merge this review PR, then install")
    ap.add_argument("--head", metavar="SHA", help="with --merge: merge only if the PR is still at this commit")
    ap.add_argument("--no-merge", type=int, metavar="PR", help="the user said no: leave this PR open, then install")
    args = ap.parse_args(argv)
    if args.max is not None and args.max < 1:
        ap.error("--max must be 1 or more")
    if not (ROOT / "config.yml").exists():
        print(f"No star-triage data repo found. Run this from your data repo, or set one up: {launcher('setup')}")
        return 1
    repo = preflight()
    if not repo:
        return 1
    if args.merge or args.no_merge:
        return answer_given(repo, args)

    run = start_run(repo, args.max, args.retriage)
    if not run:
        return 1
    done = wait(repo, run)
    if not done:
        return 1
    url = done.get("url")
    if done.get("conclusion") != "success":
        print(f"The review run ended with '{done.get('conclusion') or 'failure'}': {url}")
        reason = failure_reason(repo, str(done.get("databaseId")))
        if reason:
            print("GitHub says:\n" + "\n".join(f"  {line}" for line in reason))
        return 1
    print("The review run finished.")
    pr = review_pr(repo, when(done.get("createdAt")) or dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1))
    if not pr:
        print("No new stars to review.")
        return finish(None, None)
    count, state = pr_count(pr), str(pr.get("state", "")).upper()
    if state == "MERGED":
        print(f"Its review PR merged itself: {pr.get('url')}")
        return finish(count, None)
    if state != "OPEN":
        print(f"Its review PR was closed without merging: {pr.get('url')}")
        return finish(count, None)
    print(f"\nThe review PR waits for you: {pr.get('url')}")
    approves = pr_approves(pr)
    if approves:
        print("Merging it approves these installs (above your mode's limit, or your mode is manual):")
        for line in approves:
            print(f"  - {line}")
    print("Read their Install plan and Security scan sections in the PR before you say yes.")
    answer = ask_merge()
    if answer is None:
        number, sha = pr.get("number"), pr.get("headRefOid")
        yes, no = launcher(f"now --merge {number} --head {sha}"), launcher(f"now --no-merge {number}")
        print(f"\nANSWER NEEDED: merge review PR #{number} now?\n  yes: {yes}\n  no:  {no}")
        return 3
    if answer:
        merged = merge(repo, int(pr["number"]), str(pr.get("headRefOid") or ""))
        return finish(count, None if merged else pr_wait_line(pr))
    print("Not merged. It stays open; merge it on GitHub when you are ready.")
    return finish(count, pr_wait_line(pr))


if __name__ == "__main__":
    sys.exit(main())
