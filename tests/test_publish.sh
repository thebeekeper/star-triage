#!/usr/bin/env bash
# Checks publish.py's auto-merge decisions and the rolling review PR, against a local git
# "remote" and a fake gh. No network, no GitHub. Run: bash tests/test_publish.sh
set -euo pipefail
ENGINE="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
PY="${PYTHON:-python}"
# publish.py runs this instead of gh, and STAR_TRIAGE_NO_GH stops any `gh auth token` fallback,
# so the real gh (and your login) is never used.
export STAR_TRIAGE_GH="$ENGINE/tests/fake_gh.py" STAR_TRIAGE_NO_GH=1
export FAKE_GH_DIR="$T" FAKE_GH_LOG="$T/calls.log"
export GITHUB_REPOSITORY=o/r BASE=main GH_TOKEN=x WAITING=0

cd "$T"; git init -q --bare -b main origin.git; git clone -q origin.git seed 2>/dev/null; cd seed
git config user.name t; git config user.email t@t
cp "$ENGINE/template/config.yml" "$ENGINE/template/.gitignore" .; mkdir tracker; echo "# old" > tracker/old.md
git add -A; git commit -qm init; git push -q origin main; cd ..

note()    { printf -- '---\nrepo: %s\n---\nbody\n' "$1" > "tracker/$1.md"; }
approve() { "$PY" -c "
import json; p='installs.json'
try: d=json.load(open(p))
except Exception: d={'items':{}}
d['items']['$1']={'kind':'mcp','approved':$2,'risk':'$3','spec':{'x':1},'commit':''}
json.dump(d,open(p,'w'))"; }
fresh()   { rm -rf w; git clone -q origin.git w 2>/dev/null; cd w; mkdir -p .triage; echo "| t |" > .triage/summary.md; : > "$FAKE_GH_LOG"; }
publish() { STAR_TRIAGE_DATA="$PWD" "$PY" "$ENGINE/scripts/publish.py" >/dev/null; }
merged()  { grep -q "^pr merge" "$FAKE_GH_LOG" && echo MERGED || echo WAITS; }
cleanup() { git push -q origin --delete "$(git branch --show-current)" 2>/dev/null || true; cd ..; }
fail=0
expect()  { local got; got=$(merged); if [ "$got" = "$2" ]; then echo "ok   $1"; else echo "FAIL $1 (got $got, want $2)"; fail=1; fi; }

fresh; note a; AUTO_MERGE=notes publish;                        expect "notes: notes only merges" MERGED; cleanup
fresh; note b; approve x/b False Low; AUTO_MERGE=notes publish; expect "notes: available install merges" MERGED; cleanup
fresh; note c; approve x/c True Low; AUTO_MERGE=notes publish;  expect "notes: approved install waits" WAITS; cleanup
fresh; note d; approve x/d True Low; AUTO_MERGE=low-risk publish;    expect "low-risk: Low approval merges" MERGED; cleanup
fresh; note e; approve x/e True Medium; AUTO_MERGE=low-risk publish; expect "low-risk: Medium approval waits" WAITS; cleanup
fresh; note f; AUTO_MERGE=never publish;                        expect "never: always waits" WAITS; cleanup

# Rolling PR: a PR that waits, then main moves on, then a new run adds to the same PR.
fresh; note g; approve x/g True Medium; AUTO_MERGE=notes publish; B=$(git branch --show-current); cd ..
cd seed; git pull -q; echo "# edited" > tracker/old.md; git commit -qam "user edit"; git push -q; cd ..
fresh; git config user.name bot; git config user.email b@b
mapfile -t files < <(git diff --name-only --diff-filter=AM "origin/main...origin/$B" -- tracker installs.json)
git checkout "origin/$B" -- "${files[@]}"; git commit -qm "pending (local)"
note h; PR_NUMBER=7 PR_BRANCH="$B" AUTO_MERGE=notes STAR_TRIAGE_DATA="$PWD" "$PY" "$ENGINE/scripts/publish.py" >/dev/null
git fetch -q; changed=$(git diff --name-only origin/main...origin/"$B" | tr '\n' ' ')
case "$changed" in *tracker/g.md*tracker/h.md*) echo "ok   rolling: new note added to the open PR";;
  *) echo "FAIL rolling: PR has [$changed]"; fail=1;; esac
case "$changed" in *old.md*) echo "FAIL rolling: user's edit on main leaked into the PR"; fail=1;;
  *) echo "ok   rolling: main's changes stay out of the PR";; esac
grep -q "^pr edit" "$FAKE_GH_LOG" && echo "ok   rolling: PR description updated" || { echo "FAIL rolling: no pr edit"; fail=1; }
cd ..

fresh; note i; if FAKE=noperm STAR_TRIAGE_DATA="$PWD" "$PY" "$ENGINE/scripts/publish.py" >/dev/null 2>&1; then
  echo "FAIL noperm: should exit non-zero"; fail=1; else echo "ok   noperm: fails with a clear message"; fi
cd ..

# A changed pin on an approved item is a new approval, whether installs.json or the note's tick approved it.
pinned() { "$PY" -c "
import json; json.dump({'items':{'x/p':{'kind':'skill','approved':$1,'risk':'Low','spec':{'x':1},'commit':'$2','note':'tracker/p.md'}}},open('installs.json','w'))"
           printf -- '---
repo: p
approve: %s
---
body
' "$3" > tracker/p.md; }
cd seed; git pull -q; pinned False c1 true; git add -A; git commit -qm "ticked by the owner"; git push -q; cd ..
fresh; pinned False c1 true; note j; AUTO_MERGE=notes publish; expect "pin: ticked item, same commit merges" MERGED; cleanup
fresh; pinned False c2 true; AUTO_MERGE=notes publish;         expect "pin: ticked item, new commit waits" WAITS; cleanup
fresh; pinned True c2 false; AUTO_MERGE=notes publish;         expect "pin: unticked item, new commit merges" MERGED; cleanup
fresh; note k; AUTO_MERGE=notes publish
grep -q "^pr merge .*--match-head-commit $(git rev-parse HEAD)" "$FAKE_GH_LOG" && echo "ok   merge is tied to the pushed commit"   || { echo "FAIL merge without --match-head-commit"; fail=1; }
cleanup

# Offline approval-gate checks (reviewer-written approve, setup command allow-list).
"$PY" "$ENGINE/tests/test_approval_gates.py" || fail=1
# Offline checks that one bad repo or one bad note does not stop a run.
"$PY" "$ENGINE/tests/test_ci_resilience.py" || fail=1
exit $fail
