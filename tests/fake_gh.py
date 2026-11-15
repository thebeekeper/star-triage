#!/usr/bin/env python3
"""A stand-in for the GitHub CLI, used by test_publish.sh. Logs every call to $FAKE_GH_LOG.

FAKE=noperm makes `gh pr create` fail like a repo where Actions may not open PRs.
"""
import os
import sys

a = sys.argv[1:]
state = os.environ.get("FAKE_GH_DIR", "/tmp")
with open(os.environ.get("FAKE_GH_LOG", f"{state}/calls.log"), "a") as log:
    log.write(" ".join(x if len(x) < 60 else x[:57] + "..." for x in a) + "\n")
body = f"{state}/body.md"
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
