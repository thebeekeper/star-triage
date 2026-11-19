"""`now` (now.py): start a review run, wait for it, ask before merging, then install without the
safety delay. Offline: a temp data repo, tests/fake_gh.py for gh, a stand-in installer and a
fake clock. Run: python tests/test_now.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="star-triage-now-")).resolve()
DATA, GH = TMP / "data", TMP / "gh"
os.environ.update(STAR_TRIAGE_DATA=str(DATA), CLAUDE_CONFIG_DIR=str(TMP / "home" / ".claude"),
                  HOME=str(TMP / "home"), USERPROFILE=str(TMP / "home"), GIT_CONFIG_GLOBAL=str(TMP / "gitconfig"),
                  GIT_CONFIG_NOSYSTEM="1", STAR_TRIAGE_GH=str(ENGINE / "tests" / "fake_gh.py"),
                  STAR_TRIAGE_NO_GH="1", FAKE_GH_DIR=str(GH), FAKE_GH_LOG=str(GH / "calls.log"))
os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
(DATA / "tracker").mkdir(parents=True)
subprocess.run(["git", "init", "-q", str(DATA)], check=True)
subprocess.run(["git", "-C", str(DATA), "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
sys.path.insert(0, str(ENGINE / "scripts"))
import now  # noqa: E402
import sync  # noqa: E402

failed = 0
installed: list[str] = []
NOW = dt.datetime.now(dt.timezone.utc)


def fake_cli(self, item, action, old):
    installed.append(item["repo"])
    return {} if action == "remove" else {"via": "fake"}


sync.Sync.cli = fake_cli
sync.pull = lambda quiet: True
clock = [0.0]
now.clock = lambda: clock[0]
now.sleep = lambda s: clock.__setitem__(0, clock[0] + s)


def check(label: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"\n     {detail}" if detail and not ok else ""))
    failed += 0 if ok else 1


def stamp(minutes: float = 0) -> str:
    return (NOW + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def item(name: str, risk: str = "Low", status: str = "queued", approve: bool = True, **more) -> dict:
    text = (f'---\nrepo: "x/{name}"\nverdict: "{more.pop("verdict", "Install")}"\nrisk: "{risk}"\n'
            f"approve: {'true' if approve else 'false'}\n---\n\n# {name}\n")
    return {"repo": f"x/{name}", "kind": "cli", "name": name, "note": f"tracker/{name}.md", "risk": risk,
            "status": status, "approved": status == "queued", "reviewed_at": stamp(),
            "spec": {"registry": "npm", "package": name, "version": "1.0.0"}, "note_text": text, **more}


def setup(config: str = "mode: auto-plus\nauto_wait_hours: 24\nobsidian: false\n", items=(), prs=None,
          runs=None, **fake) -> None:
    """A fresh data repo and fake GitHub. fake: FAKE_* variables for this test only."""
    installed.clear()
    clock[0] = 0.0
    shutil.rmtree(GH, ignore_errors=True)
    GH.mkdir()
    shutil.rmtree(DATA / ".star-triage", ignore_errors=True)
    for path in (DATA / "tracker").glob("*.md"):
        path.unlink()
    for i in items:
        (DATA / i["note"]).write_text(i.pop("note_text"), encoding="utf-8")
    (DATA / "config.yml").write_text(config, encoding="utf-8")
    (DATA / "installs.json").write_text(json.dumps({"items": {i["repo"]: i for i in items}}), encoding="utf-8")
    if prs is not None:
        (GH / "prs.json").write_text(json.dumps(prs), encoding="utf-8")
    if runs is not None:
        (GH / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    for key in [k for k in os.environ if k.startswith("FAKE_") and k not in ("FAKE_GH_DIR", "FAKE_GH_LOG")]:
        del os.environ[key]
    os.environ.update({"FAKE_NOW": "1", **fake})


def pr(state: str = "MERGED", body: str = "", number: int = 7, title: str = "star-triage: 2 new repos") -> dict:
    return {"number": number, "url": f"https://github.com/o/r/pull/{number}", "state": state, "title": title,
            "headRefName": "star-triage/20261119-100000", "headRefOid": "abc123def", "isCrossRepository": False,
            "author": {"login": "app/github-actions", "is_bot": True}, "updatedAt": stamp(5), "body": body}


def run(*argv: str, answer=None) -> tuple[int, str]:
    now.ask_merge = lambda: answer
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = now.main(list(argv))
    return code, buf.getvalue()


def calls() -> str:
    try:
        return (GH / "calls.log").read_text()
    except OSError:
        return ""


WAITS = "### Run 2026-11-19\n\n| x |\n\n### Installs automatically once merged\n\n- x/m: command-line tool m@1.0.0 from npm\n"


def test_started_found_waited() -> None:
    setup(items=[item("a")], prs=[pr()], FAKE_APPEAR_AFTER="2", FAKE_DONE_AFTER="6")
    code, out = run("--max", "4", "--retriage", "x/old")
    log = calls()
    check("started: gh workflow run with the pass-through inputs",
          "workflow run star-triage.yml --repo o/r -f max=4 -f retriage=x/old" in log, log)
    check("found: the new run, after it took a few tries to show up",
          "Run: https://github.com/o/r/actions/runs/101" in out and log.count("run list") >= 3, out)
    check("waited: progress lines every ~20 s", "reviewing... 20s" in out and "reviewing... 40s" in out, out)
    check("success: merged review PR, then installs at once (no safety delay)",
          code == 0 and "merged itself" in out and installed == ["x/a"], out)
    check("summary: reviewed count and installed names",
          "Reviewed 2 repos." in out and "Installed: a." in out, out)


def test_failed_run() -> None:
    setup(items=[item("a")], prs=[pr()], FAKE_CONCLUSION="failure")
    code, out = run()
    check("failed run: prints the run URL and GitHub's reason, installs nothing",
          code == 1 and "https://github.com/o/r/actions/runs/101" in out and "No Claude credentials" in out
          and ".github#136" not in out and installed == [], out)


def test_no_new_stars() -> None:
    setup(items=[item("a")], prs=[])
    code, out = run()
    check("no new stars: says so and still installs what is approved",
          code == 0 and "No new stars to review." in out and installed == ["x/a"]
          and "No new stars were reviewed." in out, out)


def test_open_pr() -> None:
    setup(items=[item("a")], prs=[pr("OPEN", WAITS)])
    code, out = run(answer=False)
    check("open PR + no: lists what it approves, does not merge, still installs the rest",
          code == 0 and "x/m: command-line tool" in out and "pr merge" not in calls() and installed == ["x/a"], out)
    check("open PR + no: the summary says the PR waits", "review PR #7 (https://github.com/o/r/pull/7): merge it to "
          "approve x/m" in out, out)

    setup(items=[item("a")], prs=[pr("OPEN", WAITS)])
    code, out = run(answer=None)
    check("open PR, no terminal: exit 3 with the two answer commands, nothing merged or installed",
          code == 3 and "ANSWER NEEDED" in out and "now --merge 7 --head abc123def" in out
          and "now --no-merge 7" in out and "pr merge" not in calls() and installed == [], out)
    code, out = run("--merge", "7", "--head", "different")
    check("--merge refuses a PR that changed since it was shown", "changed since you saw it" in out
          and "pr merge" not in calls(), out)
    code, out = run("--merge", "7", "--head", "abc123def")
    check("--merge after a yes: merges that commit only, then installs",
          "pr merge 7 --repo o/r --squash --delete-branch --match-head-commit abc123def" in calls()
          and installed == ["x/a"] and code == 0, out)

    setup(items=[], prs=[pr("OPEN", WAITS)])
    code, out = run(answer=True)
    check("open PR + yes: merges it", code == 0 and "pr merge 7" in calls() and "Merged review PR #7." in out, out)

    setup(items=[], prs=[{**pr("OPEN", WAITS), "author": {"login": "someone"}}])
    code, out = run(answer=True)
    check("a PR someone else opened is never treated as the review PR",
          "pr merge" not in calls() and "No new stars to review." in out, out)


def test_reuses_running_run() -> None:
    running = {"databaseId": 55, "status": "in_progress", "conclusion": "", "event": "schedule",
               "createdAt": stamp(-3), "url": "https://github.com/o/r/actions/runs/55", "done_after": 2}
    setup(items=[item("a")], prs=[pr()], runs=[running])
    code, out = run()
    check("a run already going is waited for, not a second one started",
          code == 0 and "workflow run" not in calls() and "already going" in out
          and "actions/runs/55" in out and installed == ["x/a"], out)


def test_manual_mode() -> None:
    setup(config="mode: manual\nobsidian: true\n",
          items=[item("ticked", status="available", approve=True), item("wait", "Medium", "available", approve=False,
                                                                       verdict="Try")],
          prs=[])
    code, out = run()
    check("manual: says plainly what installs", "only items you ticked `approve` on, and Install verdicts" in out, out)
    check("manual: installs the ticked item only", installed == ["x/ticked"], out)
    check("manual: lists what waits for a tick, with the note name",
          "x/wait (tracker/wait.md): not ticked (verdict Try, risk Medium); to install it, tick `approve`" in out, out)


def test_safety_rules_hold() -> None:
    setup(config="mode: auto-plus\nauto_wait_hours: 24\nauto_daily_limit: 1\nobsidian: false\n",
          items=[item("one"), item("two"), item("big", "High", "available", approve=True),
                 item("bad", status="blocked", approve=True, blocked="a binary file")],
          prs=[])
    code, out = run()
    check("the 24 h safety delay is skipped, the daily limit is not; High and blocked never install",
          installed == ["x/one"], out)
    check("over the daily limit: named for tomorrow", "Tomorrow (daily limit 1): two." in out, out)
    check("High risk: not installed, listed as waiting with the reason",
          "x/big (tracker/big.md): High risk" in out, out)
    check("blocked: not installed, listed with the typed-install route",
          "x/bad (tracker/bad.md): blocked by the security scan (a binary file)" in out and "type install" in out, out)
    rows = [json.loads(line) for line in (DATA / ".star-triage" / "history.jsonl").read_text().splitlines()]
    check("recorded in the history, so undo covers it", [r["key"] for r in rows] == ["x/one"] and rows[0]["now"], rows)

    (DATA / ".star-triage" / "paused").write_text("")
    code, out = run()
    check("paused: nothing installs, and the summary says so", "paused" in out and installed == ["x/one"], out)


def test_stops_with_one_fix() -> None:
    setup(FAKE_AUTH="off")
    code, out = run()
    check("gh not logged in: one fix, nothing started", code == 1 and "Run: gh auth login" in out
          and "workflow run" not in calls(), out)
    setup(FAKE_WF_AFTER="99")
    code, out = run()
    check("workflow not registered (UT-9): one fix, nothing started",
          code == 1 and "setup --repair" in out and "workflow run" not in calls(), out)
    setup(FAKE_RUN="fail")
    code, out = run()
    check("GitHub refuses the start with a 404: the same fix", code == 1 and "setup --repair" in out, out)


def test_gives_up() -> None:
    setup(items=[item("a")], FAKE_DONE_AFTER="100000")
    keep, now.WAIT_FOR = now.WAIT_FOR, 120
    try:
        code, out = run()
    finally:
        now.WAIT_FOR = keep
    check("gives up after the time limit with the run URL, installs nothing",
          code == 1 and "Still running after 2 minutes" in out and "actions/runs/101" in out and installed == [], out)
    setup(FAKE_APPEAR_AFTER="1000")
    code, out = run()
    check("a started run that never shows up: says where to look", code == 1 and "hasn't shown up" in out, out)


def test_launcher() -> None:
    text =(ENGINE / "template" / "star-triage.py").read_text(encoding="utf-8")
    check("launcher: `now` runs now.py and is in the help", '"now": ("now.py", [])' in text
          and "\n    now  " in text)


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nall passed" if not failed else f"\n{failed} failed")
    sys.exit(1 if failed else 0)
