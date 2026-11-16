#!/usr/bin/env python3
"""A stand-in for the GitHub CLI, used by test_publish.sh and test_setup.py. Logs every call to
$FAKE_GH_LOG. The real gh (and your login) is never used.

FAKE=noperm makes `gh pr create` fail like a repo where Actions may not open PRs.
FAKE_WF_AFTER=N: GitHub Actions lists the star-triage workflow only after N calls to
  `gh api repos/<repo>/actions/workflows` (0 = at once). Counted in $FAKE_GH_DIR/wf_calls.
FAKE_RUN=fail makes `gh workflow run` fail with GitHub's HTTP 404.
FAKE_PR_PERM=off: the repo does not yet let Actions open pull requests.
"""
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


if a[:2] == ["auth", "status"]:
    sys.exit(0)
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
if a[:2] in (["secret", "list"], ["run", "list"]):
    out([])
if a[:2] == ["repo", "view"]:
    out({"defaultBranchRef": {"name": "main"}})
if a[:2] == ["workflow", "run"]:
    if os.environ.get("FAKE_RUN") == "fail":
        sys.stderr.write("could not create workflow dispatch event: HTTP 404: workflow star-triage.yml "
                         "not found on the default branch\n")
        sys.exit(1)
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
