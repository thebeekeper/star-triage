"""list, approve and unapprove (reviews.py): the review table without Obsidian. Offline: a temp
data repo only. Run: python tests/test_reviews.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="star-triage-reviews-")).resolve()
DATA = TMP / "data"
os.environ.update(STAR_TRIAGE_DATA=str(DATA), CLAUDE_CONFIG_DIR=str(TMP / "home" / ".claude"),
                  HOME=str(TMP / "home"), USERPROFILE=str(TMP / "home"))
os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
(DATA / "tracker").mkdir(parents=True)
sys.path.insert(0, str(ENGINE / "scripts"))
import reviews  # noqa: E402
import sync  # noqa: E402

failed = 0
NOW = dt.datetime.now(dt.timezone.utc)


def check(label: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"\n     {detail}" if detail and not ok else ""))
    failed += 0 if ok else 1


def ago(hours: float) -> str:
    return (NOW - dt.timedelta(hours=hours)).isoformat(timespec="seconds")


def note(name: str, verdict: str, risk: str, example: str, approve: str | None = None, scan: str = "") -> str:
    rel = f"tracker/{name}.md"
    lines = ["---", 'type: "Proxy / CLI"', f'verdict: "{verdict}"', f'risk: "{risk}"', f'repo: "x/{name}"',
             f'summary: "What {name} does."', 'reviewed: "2026-11-16"']
    if approve is not None:
        lines.append(f"approve: {approve}")
    lines += ["tags: [star-triage]", "---", "", f"# {name}", "", "## What it is", "", "A tool.", "",
              "## When you’d use it", "", f"- {example}", "- a second example", ""]
    if scan:
        lines += ["## Security scan (automated)", "", scan, ""]
    lines += ["## Links", "", "- [Repository](https://github.com/x/name)", ""]
    (DATA / rel).write_text("\n".join(lines), encoding="utf-8")
    return rel


def item(name: str, hours: float, risk: str, status: str, approved: bool, **more) -> dict:
    return {"repo": f"x/{name}", "kind": "cli", "name": name, "note": f"tracker/{name}.md", "risk": risk,
            "status": status, "approved": approved, "reviewed_at": ago(hours),
            "spec": {"registry": "npm", "package": name, "version": "1.0.0"}, **more}


def run(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = reviews.main(list(argv))
    return code, buf.getvalue()


(DATA / "config.yml").write_text("mode: auto-plus\nauto_wait_hours: 6\nobsidian: false\n", encoding="utf-8")
note("done", "Install", "Low", "you want X done", approve="true")
note("fresh", "Install", "Low", "you are in a hurry")
note("avail", "Try", "Medium", "you compare tools")
note("blocked", "Try", "Low", "you like risk", scan="- High: a binary in the repo")
note("docs", "Portfolio only", "Low", "you read docs")
note("high", "Install", "High", "you need a big hammer", approve="true")
items = [item("done", 72, "Low", "queued", True), item("fresh", 1, "Low", "queued", True),
         item("avail", 50, "Medium", "available", False),
         item("blocked", 40, "Low", "blocked", False, blocked="a binary file"),
         item("high", 30, "High", "available", False)]
(DATA / "installs.json").write_text(json.dumps({"items": {i["repo"]: i for i in items}}), encoding="utf-8")
(DATA / ".star-triage").mkdir()
(DATA / ".star-triage" / "installed.json").write_text(json.dumps({"x/done": {"kind": "cli", "fp": "x"}}),
                                                      encoding="utf-8")

try:
    code, out = run("list")
    lines = out.splitlines()
    rows = {line.split()[0]: line for line in lines[1:] if line.startswith("x/")}
    order = [line.split()[0] for line in lines[1:] if line.startswith("x/")]
    check("list: one header and one row per reviewed repo",
          code == 0 and lines[0].split()[:4] == ["Repo", "Kind", "Verdict", "Risk"] and len(rows) == 6, out)
    check("list: newest review first", order[0] == "x/fresh" and order.index("x/high") < order.index("x/blocked")
          < order.index("x/avail") < order.index("x/done"), str(order))
    when = (NOW - dt.timedelta(hours=1) + dt.timedelta(hours=6)).astimezone()
    stamp = when.strftime("%H:%M") if when.date() == dt.datetime.now().astimezone().date() else when.strftime("%a %H:%M")
    check("list: installed / installs after HH:MM / waiting for you / blocked / nothing to install",
          "installed" in rows["x/done"] and f"installs after {stamp}" in rows["x/fresh"]
          and "waiting for you" in rows["x/avail"] and "blocked" in rows["x/blocked"]
          and "nothing to install" in rows["x/docs"], out)
    check("list: a High-risk item the user ticked still waits for them in auto-plus", "waiting for you" in rows["x/high"],
          rows["x/high"])
    check("list: kind, verdict, risk and the first 'When you'd use it' example",
          "CLI tool" in rows["x/fresh"] and "Install" in rows["x/fresh"] and "you are in a hurry" in rows["x/fresh"]
          and "a second example" not in out and "Proxy / CLI" in rows["x/docs"], out)

    code, out = run("list", "--waiting")
    shown = sorted(line.split()[0] for line in out.splitlines() if line.startswith("x/"))
    check("list --waiting: only what needs the user", shown == ["x/avail", "x/blocked", "x/high"], out)

    code, out = run("list", "--repo", "x/blocked")
    check("list --repo: summary, every example and the security findings",
          code == 0 and "What blocked does." in out and "you like risk" in out and "a second example" in out
          and "Security scan:" in out and "a binary in the repo" in out and "Status: blocked" in out, out)
    check("list --repo: an unknown repo exits 1", run("list", "--repo", "x/nope")[0] == 1)

    before = (DATA / "tracker" / "avail.md").read_text(encoding="utf-8")
    code, out = run("approve", "avail")
    after = (DATA / "tracker" / "avail.md").read_text(encoding="utf-8")
    check("approve: a bare unique name works; adds `approve: true` to the frontmatter and nothing else",
          code == 0 and after.replace("\napprove: true", "", 1) == before and sync.note_approve("tracker/avail.md") is True,
          out)
    check("approve: then sync treats it as approved", "x/avail" in sync.wanted(sync.load_json(sync.INSTALLS_PATH, {})
                                                                                 ["items"]), out)
    check("approve: says when it installs, and how to install now",
          "installs the next time Claude Code starts" in out and "/star-triage:sync now" in out, out)
    code, out = run("unapprove", "x/avail")
    check("unapprove: the same line becomes false", code == 0 and sync.note_approve("tracker/avail.md") is False
          and (DATA / "tracker" / "avail.md").read_text(encoding="utf-8").count("approve:") == 1, out)

    code, out = run("approve", "x/blocked")
    check("approve on a blocked item: ticked, but it still needs sync in a terminal and a typed install",
          code == 0 and sync.note_approve("tracker/blocked.md") is True and "type install" in out
          and "x/blocked" not in [k for _, k, _ in sync.hold_back(
              sync.plan(sync.load_json(sync.INSTALLS_PATH, {})["items"], {}, False), 0, 5, [])[1]], out)
    code, out = run("list")
    check("list: a ticked blocked item is still shown as blocked",
          "blocked" in next(line for line in out.splitlines() if line.startswith("x/blocked")), out)

    before = (DATA / "tracker" / "docs.md").read_text(encoding="utf-8")
    code, out = run("approve", "x/docs")
    check("approve: a repo with nothing to install is refused, its note unchanged",
          code == 1 and (DATA / "tracker" / "docs.md").read_text(encoding="utf-8") == before, out)
    check("approve: an unknown repo exits 1", run("approve", "x/nope")[0] == 1)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
