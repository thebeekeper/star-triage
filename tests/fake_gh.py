#!/usr/bin/env python3
"""A stand-in for the GitHub CLI, used by test_publish.sh and test_setup.py. Logs every call to
$FAKE_GH_LOG. The real gh (and your login) is never used.

FAKE=noperm makes `gh pr create` fail like a repo where Actions may not open PRs.
FAKE_WF_AFTER=N: GitHub Actions lists the star-triage workflow only after N calls to
  `gh api repos/<repo>/actions/workflows` (0 = at once). Counted in $FAKE_GH_DIR/wf_calls.
FAKE_RUN=fail makes `gh workflow run` fail with GitHub's HTTP 404.
FAKE_PR_PERM=off: the repo does not yet let Actions open pull requests.
FAKE_SECRET=1: the repo has CLAUDE_CODE_OAUTH_TOKEN (names only, as `gh secret list` gives them).
FAKE_AUTH=off: `gh auth status` fails (not logged in).

Workflow runs and review PRs (test_now.py), kept in $FAKE_GH_DIR:
  runs.json  the workflow's runs, newest first. `gh run list` prints them (filtered by --event);
             without this file it prints []. Each run may carry test-only keys:
               appear_after N  listed only after N `gh run list` calls
               done_after N    `gh run view --json` says in_progress until its N-th call
               result          its conclusion once completed (default success)
  FAKE_NOW=1 makes `gh workflow run` add a new workflow_dispatch run to runs.json, using
             FAKE_APPEAR_AFTER, FAKE_DONE_AFTER and FAKE_CONCLUSION for those keys.
  prs.json   pull requests for `gh pr list` and `gh pr view N --json ...`.
"""
import datetime as dt
import json
import os
import sys

a = sys.argv[1:]
state = os.environ.get("FAKE_GH_DIR", "/tmp")
with open(os.environ.get("FAKE_GH_LOG", f"{state}/calls.log"), "a") as log:
    log.write(" ".join(x if len(x) < 60 else x[:57] + "..." for x in a) + "\n")
body = f"{state}/body.md"


def out(value) -> None:
    print(json.dumps(value))
    sys.exit(0)


def wf_registered(count: bool) -> bool:
    path = f"{state}/wf_calls"
    calls = int(open(path).read() or 0) if os.path.exists(path) else 0
    if count:
        calls += 1
        open(path, "w").write(str(calls))
    return calls > int(os.environ.get("FAKE_WF_AFTER", "0"))


def load(name: str, default):
    try:
        return json.load(open(f"{state}/{name}"))
    except (OSError, ValueError):
        return default


def save(name: str, value) -> None:
    json.dump(value, open(f"{state}/{name}", "w"))


def bump(name: str) -> int:
    calls = load(name, 0) + 1
    save(name, calls)
    return calls


def public(run: dict) -> dict:
    return {k: v for k, v in run.items() if k not in ("appear_after", "done_after", "result", "views")}


if a[:2] == ["auth", "status"]:
    sys.exit(1 if os.environ.get("FAKE_AUTH") == "off" else 0)
if a[:2] == ["run", "list"] and os.path.exists(f"{state}/runs.json"):
    calls = bump("run_list_calls")
    event = a[a.index("--event") + 1] if "--event" in a else None
    out([public(r) for r in load("runs.json", []) if calls > r.get("appear_after", 0)
         and (not event or r.get("event") == event)])
if a[:2] == ["run", "view"]:
    runs = load("runs.json", [])
    run = next((r for r in runs if str(r.get("databaseId")) == a[2]), None)
    if run is None:
        sys.exit(1)
    if "--json" not in a:
        if run.get("result", "success") != "success":
            print("\n".join([f"X main star-triage - {a[2]}", "", "JOBS", "X triage in 40s", "", "ANNOTATIONS",
                             "X No Claude credentials", "triage: .github#136",
                             "This repo has no CLAUDE_CODE_OAUTH_TOKEN secret.", "",
                             f"To see what failed, try: gh run view {a[2]} --log-failed",
                             f"View this run on GitHub: {run['url']}"]))
        sys.exit(0)
    run["views"] = run.get("views", 0) + 1
    if run["views"] >= run.get("done_after", 1):
        run["status"], run["conclusion"] = "completed", run.get("result", "success")
    else:
        run["status"] = "in_progress"
    save("runs.json", runs)
    out(public(run))
if a[:2] == ["pr", "list"]:
    out(load("prs.json", []))
if a[:2] == ["pr", "view"] and "--json" in a and os.path.exists(f"{state}/prs.json"):
    pr = next((p for p in load("prs.json", []) if str(p.get("number")) == a[2]), None)
    if pr is None:
        sys.exit(1)
    out(pr)
if a[:1] == ["api"] and "-X" in a:  # a settings change
    sys.exit(0)
if a[:1] == ["api"] and len(a) == 2:
    path = a[1]
    if path == "user":
        out({"login": "tester", "id": 42})
    if path.endswith("/actions/workflows"):
        out({"total_count": 1, "workflows": [{"name": "star-triage", "path": ".github/workflows/star-triage.yml",
                                              "state": "active"}] if wf_registered(True) else []})
    if path.endswith("/actions/workflows/star-triage.yml"):
        if wf_registered(False):
            out({"state": "active"})
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        sys.exit(1)
    if "/contents/" in path:
        out({"path": path.split("/contents/", 1)[1]})
    if path.endswith("/actions/permissions"):
        out({"enabled": True})
    if path.endswith("/actions/permissions/workflow"):
        out({"default_workflow_permissions": "read",
             "can_approve_pull_request_reviews": os.environ.get("FAKE_PR_PERM") != "off"})
    if path.startswith("repos/"):
        out({"full_name": path[6:]})
if a[:2] == ["secret", "list"] and os.environ.get("FAKE_SECRET") == "1":
    out([{"name": "CLAUDE_CODE_OAUTH_TOKEN"}])
if a[:2] in (["secret", "list"], ["run", "list"]):
    out([])
if a[:2] == ["repo", "view"]:
    out({"defaultBranchRef": {"name": "main"}})
if a[:2] == ["workflow", "run"]:
    if os.environ.get("FAKE_RUN") == "fail":
        sys.stderr.write("could not create workflow dispatch event: HTTP 404: workflow star-triage.yml "
                         "not found on the default branch\n")
        sys.exit(1)
    if os.environ.get("FAKE_NOW") == "1":
        runs = load("runs.json", [])
        rid = max([r.get("databaseId", 0) for r in runs] + [100]) + 1
        runs.insert(0, {"databaseId": rid, "status": "queued", "conclusion": "", "event": "workflow_dispatch",
                        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                        "url": f"https://github.com/o/r/actions/runs/{rid}",
                        "appear_after": int(os.environ.get("FAKE_APPEAR_AFTER", "0")),
                        "done_after": int(os.environ.get("FAKE_DONE_AFTER", "1")),
                        "result": os.environ.get("FAKE_CONCLUSION", "success")})
        save("runs.json", runs)
    sys.exit(0)
if a[:2] == ["pr", "create"]:
    if os.environ.get("FAKE") == "noperm":
        sys.stderr.write("pull request create failed: GraphQL: GitHub Actions is not permitted to create "
                         "or approve pull requests (createPullRequest)\n")
        sys.exit(1)
    open(body, "w").write(open(a[a.index("--body-file") + 1]).read())
    print("https://github.com/o/r/pull/7")
    sys.exit(0)
if a[:2] == ["pr", "view"]:
    print(open(body).read())
    sys.exit(0)
if a[:2] == ["pr", "edit"]:
    open(body, "w").write(open(a[a.index("--body-file") + 1]).read())
    sys.exit(0)
if a[:2] == ["pr", "merge"]:
    sys.exit(0)
sys.exit(1)
