"""Your reviews without Obsidian.

    python star-triage.py list                     every reviewed repo, newest first
    python star-triage.py list --waiting           only the ones that need you
    python star-triage.py list --repo owner/name   one review: summary, examples, security findings
    python star-triage.py approve owner/name       approve its install, as ticking `approve` in Obsidian does
    python star-triage.py unapprove owner/name     untick it (an installed item is then removed)

First each command fetches the latest reviews (`git pull --ff-only --autostash`, as sync does);
if that fails, it uses the notes on this computer and says so.

Status: installed / installs at next start / installs after HH:MM (auto modes' safety delay) /
installs tomorrow (daily limit) / waiting for you / blocked / paused / nothing to install.
approve and unapprove change only the `approve` line of the note, exactly like the checkbox in
Obsidian, and the same rules follow: a blocked item still needs `python star-triage.py sync` in
your own terminal and a typed "install", and High-risk items never install by themselves in the
auto modes. Only the Python standard library is needed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync  # noqa: E402  (finds the data repo, see paths.py)
from paths import NOW_HINT, safe_console  # noqa: E402

ROOT = sync.ROOT
INSTALLABLE = ("plugin", "skill", "mcp", "cli")
KIND_NAMES = {"plugin": "plugin", "skill": "skill", "mcp": "MCP server", "cli": "CLI tool"}
WAITING = ("waiting for you", "blocked")
USE_TITLE = r"When you.d use it"


def front(text: str) -> tuple[dict, str]:
    """(frontmatter, body) of a note, read without PyYAML. Lists stay as their raw text."""
    m = sync.FRONT.match(text)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t#-" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                value = value.strip('"')
        elif value.startswith("'"):
            value = value.strip("'")
        fm[key.strip()] = value
    return fm, text[m.end():]


def section(body: str, title: str) -> str:
    """The text under `## <title>` (a regex), up to the next `## ` heading."""
    m = re.search(rf"^## {title}[ \t]*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    return m.group(1).strip() if m else ""


def bullets(text: str) -> list[str]:
    return [line.strip()[2:].strip() for line in text.splitlines() if line.strip().startswith(("- ", "* "))]


def load_notes() -> list[dict]:
    """Every review note in tracker/, with its install item (if any)."""
    items = sync.load_json(sync.INSTALLS_PATH, {}).get("items", {})
    by_note = {str(it.get("note")): (key, it) for key, it in items.items() if it.get("note")}
    notes = []
    for path in sorted((ROOT / "tracker").glob("*.md")):
        if path.name.startswith("_") or path.name.lower() == "readme.md":
            continue
        try:
            fm, body = front(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm.get("repo") or "star-triage-template" in str(fm.get("tags", "")):
            continue
        rel = path.relative_to(ROOT).as_posix()
        key, item = by_note.get(rel) or (fm["repo"], items.get(fm["repo"]))
        notes.append({"note": rel, "fm": fm, "body": body, "key": key, "item": item,
                      "when": str((item or {}).get("reviewed_at") or fm.get("reviewed") or fm.get("added") or "")})
    notes.sort(key=lambda n: n["when"], reverse=True)
    return notes


def statuses(notes: list[dict], now: dt.datetime | None = None) -> dict[str, str]:
    """note path -> status, worked out the way sync.py's background run would act."""
    items = sync.load_json(sync.INSTALLS_PATH, {}).get("items", {})
    state = sync.load_json(sync.STATE_PATH, {})
    mode, wait_h, limit = sync.settings()
    approved = sync.wanted(items)
    steps = [s for s in sync.plan(items, state, prune=False) if s[0] != "remove"]
    waits, capped, high = {}, set(), set()
    if mode != "manual":
        _, waiting, capped_steps, high_steps = sync.hold_back(steps, wait_h, limit, sync.history_read(), now)
        waits = {k: sync.local_time(sync.reviewed_time(i) + dt.timedelta(hours=wait_h), now) for _, k, i in waiting}
        capped, high = {k for _, k, _ in capped_steps}, {k for _, k, _ in high_steps}
    paused = sync.PAUSE_PATH.exists()
    out = {}
    for n in notes:
        key, item = n["key"], n["item"] or {}
        if item.get("kind") not in INSTALLABLE or item.get("status") not in ("queued", "available", "blocked"):
            status = "nothing to install"
        elif key in state:
            status = "installed"
        elif item.get("status") == "blocked":
            status = "blocked"
        elif key not in approved or key in high:
            status = "waiting for you"
        elif paused:
            status = "paused"
        elif key in waits:
            status = f"installs after {waits[key]}"
        elif key in capped:
            status = "installs tomorrow"
        else:
            status = "installs at next start"
        out[n["note"]] = status
    return out


def kind_of(n: dict) -> str:
    """One style for every row (UT-23): lowercase, acronyms kept ('MCP server', 'CLI tool', 'app')."""
    kind = (n["item"] or {}).get("kind")
    if kind in KIND_NAMES:
        return KIND_NAMES[kind]
    words = str(n["fm"].get("type") or "-").lower().split()
    return " ".join(w.upper() if w in ("mcp", "cli", "ui", "api") else w for w in words)


def cut(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + "..."


def table(notes: list[dict], status: dict[str, str]) -> list[str]:
    head = ("Repo", "Kind", "Verdict", "Risk", "Status", "When you'd use it")
    rows = [(n["fm"].get("repo", ""), kind_of(n), n["fm"].get("verdict") or "-", n["fm"].get("risk") or "-",
             status[n["note"]], (bullets(section(n["body"], USE_TITLE)) or ["-"])[0]) for n in notes]
    widths = [min(max(len(str(r[i])) for r in rows + [head]), w) for i, w in enumerate((36, 12, 14, 6, 24))]
    lines = []
    for r in [head] + rows:
        cells = [cut(r[i], widths[i]).ljust(widths[i]) for i in range(5)]
        lines.append("  ".join(cells + [cut(r[5], 70)]).rstrip())
    return lines


def find(notes: list[dict], want: str) -> dict | None:
    """A note by owner/name, or by name alone when only one repo has that name."""
    want = want.strip().lower()
    exact = [n for n in notes if str(n["fm"].get("repo", "")).lower() == want]
    if exact:
        return exact[0]
    named = [n for n in notes if str(n["fm"].get("repo", "")).lower().rsplit("/", 1)[-1] == want.rsplit("/", 1)[-1]]
    return named[0] if len(named) == 1 else None


def show_one(n: dict, status: str) -> None:
    fm = n["fm"]
    print(f"{fm.get('repo')}  ({kind_of(n)}, verdict {fm.get('verdict') or '-'}, risk {fm.get('risk') or '-'})")
    print(f"Status: {status}")
    if fm.get("summary"):
        print(f"\n{fm['summary']}")
    examples = bullets(section(n["body"], USE_TITLE))
    if examples:
        print("\nWhen you'd use it:")
        for line in examples:
            print(f"  - {line}")
    for title, label in ((r"Security scan \(automated\)", "Security scan"), ("Risks", "Risks")):
        text = section(n["body"], title)
        if text:
            print(f"\n{label}:")
            for line in text.splitlines():
                print(f"  {line}" if line.strip() else "")
    print(f"\nNote: {n['note']}")


def set_approve(want: str, value: bool) -> int:
    notes = load_notes()
    n = find(notes, want)
    if not n:
        print(f"No review of '{want}' found. See them all: python star-triage.py list")
        return 1
    item, repo = n["item"] or {}, n["fm"].get("repo")
    if value and item.get("kind") not in INSTALLABLE:
        print(f"{repo} has nothing star-triage can install, so approving it would do nothing.")
        return 1
    sync.set_note_approve(n["note"], value)
    print(f"{'Ticked' if value else 'Unticked'} approve on {n['note']}.")
    if not value:
        if n["key"] in sync.load_json(sync.STATE_PATH, {}):
            print("It is removed the next time Claude Code starts (or now: python star-triage.py sync).")
    else:
        status = statuses([n])[n["note"]]
        mode = sync.settings()[0]
        if item.get("status") == "blocked":
            print(f"The security scan blocked it ({item.get('blocked') or 'see its note'}), so it never installs by "
                  "itself. To install it anyway, run in your own terminal: python star-triage.py sync, and type install.")
        elif mode != "manual" and item.get("risk") == "High":
            print("It is High risk, so it never installs by itself. To install it, run in your own terminal: "
                  "python star-triage.py sync")
        elif status.startswith("installs after"):
            print(f"It {status} (the safety delay of your star-triage mode). {NOW_HINT}")
        elif status == "paused":
            print("star-triage is paused on this computer: run python star-triage.py resume first.")
        elif status != "installed":
            print(f"It installs the next time Claude Code starts. {NOW_HINT}")
    print("(Changed on this computer, like ticking the box in Obsidian. Commit and push the note to keep it on GitHub.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    safe_console()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in ("list", "approve", "unapprove"):
        print(__doc__)
        return 0 if not argv or argv[0] in ("-h", "--help") else 1
    if not (ROOT / "config.yml").exists() or not (ROOT / "tracker").is_dir():
        print("No star-triage data repo found. Run this from your data repo, or set one up: /star-triage:setup")
        return 1
    command, rest = argv[0], argv[1:]
    if not sync.pull(quiet=True):
        print("Couldn't fetch the latest reviews from GitHub (git pull failed), so this uses the notes on this "
              "computer.\n")
    if command in ("approve", "unapprove"):
        if len(rest) != 1:
            print(f"Usage: python star-triage.py {command} owner/name")
            return 1
        return set_approve(rest[0], command == "approve")
    notes = load_notes()
    if "--repo" in rest:
        at = rest.index("--repo")
        want = rest[at + 1] if at + 1 < len(rest) else ""
        n = find(notes, want) if want else None
        if not n:
            print(f"No review of '{want}' found. See them all: python star-triage.py list")
            return 1
        show_one(n, statuses([n])[n["note"]])
        return 0
    status = statuses(notes)
    if "--waiting" in rest:
        notes = [n for n in notes if status[n["note"]] in WAITING]
    if not notes:
        print("Nothing waits for you." if "--waiting" in rest else
              "No reviews yet. They arrive after a run on GitHub, once its pull request is merged and pulled.")
        return 0
    for line in table(notes, status):
        print(line)
    print(f"\n{len(notes)} repo(s). One in full: python star-triage.py list --repo owner/name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
