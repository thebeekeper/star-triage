"""Auto mode checks. Run: python tests/test_auto_mode.py

No network, no gh, no real ~/.claude and nothing really installed: HOME, CLAUDE_CONFIG_DIR and
the data repo all point into one temp folder, and the installer for command-line tools is
replaced by a stand-in that only records what it was asked to do.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="star-triage-auto-")).resolve()
DATA = TMP / "data"
CLAUDE = TMP / "home" / ".claude"
failed = 0

os.environ.update(STAR_TRIAGE_DATA=str(DATA), CLAUDE_CONFIG_DIR=str(CLAUDE), HOME=str(TMP / "home"),
                  USERPROFILE=str(TMP / "home"), GIT_CONFIG_GLOBAL=str(TMP / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
(DATA / "tracker").mkdir(parents=True)
CLAUDE.mkdir(parents=True)
sys.argv = ["sync.py", "--background"]  # sync.py decides at import time that this is an automatic run
sys.path.insert(0, str(ENGINE / "scripts"))
import paths  # noqa: E402
import sync  # noqa: E402

calls = []


def fake_cli(self, item, action, old):
    calls.append((action, item["repo"]))
    return {} if action == "remove" else {"via": "fake"}


sync.Sync.cli = fake_cli
sync.pull = lambda quiet: None


def check(label: str, ok: bool) -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    failed += 0 if ok else 1


def ago(hours: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat(timespec="seconds")


def item(name: str, hours: float = 48, risk: str = "Low", approved: bool = True, **more) -> dict:
    note = f"tracker/{name}.md"
    (DATA / note).write_text(f"---\nrepo: x/{name}\napprove: {'true' if approved else 'false'}\n---\nbody\n",
                             encoding="utf-8")
    return {"repo": f"x/{name}", "kind": "cli", "name": name, "note": note, "approved": approved, "risk": risk,
            "status": "queued" if approved else "available", "reviewed_at": ago(hours),
            "spec": {"registry": "npm", "package": name, "version": "1.0.0"}, **more}


def world(config: str, items: list[dict]) -> None:
    """A fresh data repo state: config, installs.json, nothing installed, no history."""
    shutil.rmtree(DATA / ".star-triage", ignore_errors=True)
    (DATA / "config.yml").write_text(config, encoding="utf-8")
    (DATA / "installs.json").write_text(json.dumps({"items": {i["repo"]: i for i in items}}), encoding="utf-8")
    calls.clear()


def run(*flags: str) -> int:
    sys.argv = ["sync.py", *flags]
    out, sys.stdout = sys.stdout, open(os.devnull, "w")
    try:
        return sync.main()
    finally:
        sys.stdout.close()
        sys.stdout = out


def installed() -> list[str]:
    return sorted(repo for action, repo in calls if action == "install")


def test_mapping() -> None:
    read, policy, may = paths.read_mode, paths.merge_policy, paths.may_merge
    check("mode: nothing set is manual", read({}) == "manual")
    check("mode: auto_merge notes is manual", read({"auto_merge": "notes"}) == "manual")
    check("mode: auto_merge low-risk is auto", read({"auto_merge": "low-risk"}) == "auto")
    check("mode: mode wins over auto_merge", read({"mode": "manual", "auto_merge": "low-risk"}) == "manual"
          and read({"mode": "auto-plus", "auto_merge": "never"}) == "auto-plus")
    check("mode: an unknown mode falls back to auto_merge", read({"mode": "turbo", "auto_merge": "low-risk"}) == "auto")
    check("mode: old auto_merge never still always waits", policy({"auto_merge": "never"}) == "never"
          and not may("never", []))
    check("merge: manual merges notes only", may("manual", []) and not may("manual", ["Low"]))
    check("merge: auto merges Low only", may("auto", ["Low", "Low"]) and not may("auto", ["Low", "Medium"]))
    check("merge: auto-plus merges Low and Medium", may("auto-plus", ["Low", "Medium"]))
    check("merge: High never merges itself", not any(may(m, ["High"]) for m in ("manual", "auto", "auto-plus")))
    check("merge: an unknown risk never merges itself", not may("auto-plus", [""]) and not may("auto-plus", [None]))
    check("merge: names used before 5.2 still work", may("notes", []) and may("low-risk", ["Low"])
          and not may("low-risk", ["Medium"]))


def test_wait_cap_pause() -> None:
    world("mode: manual\n", [item("fresh", hours=1)])
    run("--background")
    check("manual: installs an approved item at once (as before)", installed() == ["x/fresh"])

    world("mode: auto\n", [item("fresh", hours=1), item("old", hours=7)])
    run("--background")
    check("auto: an item reviewed 1 h ago waits, one reviewed 7 h ago installs", installed() == ["x/old"])
    world("mode: auto\nauto_wait_hours: 0\n", [item("fresh", hours=1)])
    run("--background")
    check("auto: auto_wait_hours 0 installs at once", installed() == ["x/fresh"])
    world("auto_merge: low-risk\n", [item("fresh", hours=1)])
    run("--background")
    check("auto: the old auto_merge low-risk key gives the same delay", installed() == [])

    world("mode: auto\nauto_daily_limit: 2\n", [item(n) for n in ("a", "b", "c", "d")])
    run("--background")
    check("auto: the daily limit stops at 2 of 4", len(installed()) == 2)
    calls.clear()
    run("--background")
    check("auto: a second run on the same day installs no more", installed() == [])
    check("auto: the default limit is 5 and the default delay 6 h", sync.settings() == ("auto", 6, 2)
          and paths.daily_limit({}) == 5 and paths.wait_hours({}) == 6)

    world("mode: auto-plus\n", [item("high", risk="High"), item("medium", risk="Medium"),
                                item("blocked", status="blocked", blocked="test")])
    run("--background")
    check("auto-plus: High risk and blocked are never installed in the background", installed() == ["x/medium"])
    world("mode: auto\n", [item("notyet", approved=False)])
    run("--background")
    check("auto: an item nobody approved is not installed", installed() == [])

    for mode in ("manual", "auto"):
        world(f"mode: {mode}\n", [item("old")])
        run("--pause")
        run("--background")
        check(f"pause ({mode}): nothing installs", installed() == [])
        run("--resume")
        run("--background")
        check(f"resume ({mode}): installs again", installed() == ["x/old"])


def test_history_undo() -> None:
    world("mode: auto\n", [item("one"), item("two")])
    run("--background")
    rows = sync.history_read()
    check("history: one line per automatic install, with a reason",
          [r["key"] for r in rows] == ["x/one", "x/two"] and all(r["auto"] and "Low risk" in r["why"] for r in rows))

    # A later review downgrades x/one: approved turns false and so does the note's box.
    downgraded = item("one", approved=False)
    (DATA / "installs.json").write_text(json.dumps({"items": {"x/one": downgraded, "x/two": item("two")}}),
                                        encoding="utf-8")
    calls.clear()
    run("--background")
    check("downgrade: the item is removed automatically", calls == [("remove", "x/one")])
    check("downgrade: logged as 'removed: downgraded by re-review'",
          sync.history_read()[-1]["why"] == "removed: downgraded by re-review")

    world("mode: auto\n", [item("first")])
    run("--background")
    (DATA / "installs.json").write_text(json.dumps({"items": {i["repo"]: i for i in
                                                              (item("first"), item("second"), item("third"))}}),
                                        encoding="utf-8")
    result = sync.load_json(sync.RESULT_PATH, {})
    time.sleep(1.1)  # a batch is named by its start time, to the second
    run("--background")
    check("undo: two batches are on record", len({r["batch"] for r in sync.history_read()}) == 2
          and result.get("at") != sync.load_json(sync.RESULT_PATH, {}).get("at"))
    calls.clear()
    sync.input = lambda prompt="": "n"
    run("--undo")
    check("undo: answering n changes nothing", calls == [] and len(sync.load_json(sync.STATE_PATH, {})) == 3)
    sync.input = lambda prompt="": "y"
    run("--undo")
    check("undo: removes the last batch only", sorted(calls) == [("remove", "x/second"), ("remove", "x/third")]
          and list(sync.load_json(sync.STATE_PATH, {})) == ["x/first"])
    check("undo: unticks those notes and leaves the others",
          sync.note_approve("tracker/second.md") is False and sync.note_approve("tracker/third.md") is False
          and sync.note_approve("tracker/first.md") is True)
    calls.clear()
    run("--background")
    check("undo: the removed items are not installed again", calls == [])
    check("history: the undo is on record", [r["why"] for r in sync.history_read()[-2:]] == ["removed: undone by you"] * 2)


def hook() -> str:
    (CLAUDE / "star-triage.json").write_text(json.dumps({"data": str(DATA)}), encoding="utf-8")
    (DATA / ".star-triage" / "hook.json").unlink(missing_ok=True)  # let it check again right away
    res = subprocess.run([sys.executable, str(ENGINE / "scripts" / "sync.py"), "--hook"], cwd=TMP, env=dict(os.environ),
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    return (json.loads(res.stdout or "{}").get("systemMessage") or "") + ("" if res.returncode == 0 else res.stderr)


def test_hook_message() -> None:
    later = item("later", hours=1)
    world("mode: auto\n", [item("context7"), item("ccusage"), later])
    run("--background")
    at = (sync.reviewed_time(later) + dt.timedelta(hours=6)).astimezone()
    when = at.strftime("%H:%M") if at.date() == dt.datetime.now().astimezone().date() else at.strftime("%a %H:%M")
    want = (f"star-triage auto: installed ccusage, context7; later installs after {when} (6 h safety delay) "
            "(install now: /star-triage:sync now)")
    line = hook()
    check("hook: one line says what the star-triage mode did, what waits until when (local time), and how to "
          "install it now", line == want)
    if line != want:
        print("     got:", repr(line), "\n     want:", repr(want))

    now = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.timezone.utc)
    check("hook: a time later today is HH:MM, another day gets the weekday",
          sync.local_time(now + dt.timedelta(minutes=1), now) == (now + dt.timedelta(minutes=1)).astimezone().strftime("%H:%M")
          and sync.local_time(now + dt.timedelta(days=2), now) == (now + dt.timedelta(days=2)).astimezone().strftime("%a %H:%M"))
    waits = [("install", f"x/w{n}", item(f"w{n}", hours=1)) for n in range(5)]
    text = sync.waiting_text(waits, 6)
    check("hook: several waiting items are named, the rest counted",
          text.startswith("w0, w1, w2 and 2 more install after ") and text.endswith(" (6 h safety delay)"))
    pair = [("install", "x/ponytail", item("ponytail", hours=1)), ("install", "x/brag", item("brag", hours=1)),
            ("install", "x/later", item("later", hours=0))]
    text = sync.waiting_text(pair, 1)
    check("hook: items that share a time are grouped (UT-24)",
          text.startswith("ponytail and brag install after ") and ", later after " in text
          and text.endswith(" (1 h safety delay)"))

    # a1 installs now (in this process, with the stand-in installer), so the hook finds the
    # limit used up and starts no background install of its own.
    world("mode: auto-plus\nauto_daily_limit: 1\n", [item("a1"), item("a2"), item("a3")])
    run("--background")
    line = hook()
    check("hook: items over the daily limit are named", "a2, a3 wait for tomorrow (daily limit 1)" in line)
    if "wait for tomorrow" not in line:
        print("     got:", repr(line))

    world("mode: manual\n", [])
    installs = json.loads((DATA / "installs.json").read_text(encoding="utf-8"))
    installs["engine_version"] = "99.0.0"
    (DATA / "installs.json").write_text(json.dumps(installs), encoding="utf-8")
    line = hook()
    check("hook: an older plugin than the reviews' engine is named, with the update commands",
          "your reviews use 99.0.0" in line and "claude plugin marketplace update star-triage" in line
          and "claude plugin update star-triage@star-triage" in line)
    if "99.0.0" not in line:
        print("     got:", repr(line))


def test_mode_command() -> None:
    """mode.py edits config.yml, commits and pushes (to a local bare repo)."""
    repo, origin = TMP / "moderepo", TMP / "origin.git"
    (repo / "tracker").mkdir(parents=True)

    def git(*args: str, cwd: Path = repo) -> str:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()

    def mode(*args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, STAR_TRIAGE_DATA=str(repo))
        return subprocess.run([sys.executable, str(ENGINE / "scripts" / "mode.py"), *args], cwd=repo, env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git("init", "-q", "-b", "main")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t")
    git("remote", "add", "origin", str(origin))
    old = "# my settings\n\nauto_merge: low-risk\nabout_me: >\n  mode: not a setting\n"
    (repo / "config.yml").write_text(old, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    git("push", "-qu", "origin", "main")

    res = mode()
    check("mode: with no word it shows the current mode and the three choices, and changes nothing",
          res.returncode == 0 and "star-triage mode: auto\n" in res.stdout and all(m in res.stdout for m in paths.MODES)
          and (repo / "config.yml").read_text(encoding="utf-8") == old)
    res = mode("auto-plus")
    text = (repo / "config.yml").read_text(encoding="utf-8")
    check("mode auto-plus: writes mode near the top with its comment, keeps the rest",
          "\nmode: auto-plus\n" in text and text.startswith("# my settings\n") and text.count("# ") == 4
          and text.endswith(old.split("\n\n", 1)[1]))
    check("mode auto-plus: commits 'Switch star-triage to auto-plus' and pushes",
          git("log", "-1", "--format=%s") == "Switch star-triage to auto-plus"
          and git("rev-parse", "HEAD") == git("rev-parse", "main", cwd=origin))
    check("mode auto-plus: one confirmation line", res.returncode == 0
          and res.stdout.strip().startswith("star-triage is now in auto-plus mode") and len(res.stdout.strip().splitlines()) == 1)
    mode("manual")
    check("mode manual: replaces the line instead of adding one",
          (repo / "config.yml").read_text(encoding="utf-8").count("\nmode: ") == 1
          and paths.read_mode(paths.read_simple_config(repo / "config.yml")) == "manual")
    check("mode: a wrong word changes nothing", mode("turbo").returncode == 1
          and git("log", "-1", "--format=%s") == "Switch star-triage to manual")
    git("remote", "set-url", "origin", str(TMP / "missing.git"))
    res = mode("auto")
    check("mode: a failed push says so and gives the command to run",
          res.returncode == 1 and "git push failed" in res.stdout and "\n  git push" in res.stdout
          and git("log", "-1", "--format=%s") == "Switch star-triage to auto")


def test_now() -> None:
    """sync --now skips only the safety delay."""
    fresh = [item(n, hours=0.5) for n in ("n1", "n2", "n3")]
    world("mode: auto-plus\nauto_wait_hours: 6\nauto_daily_limit: 2\n",
          fresh + [item("nhigh", hours=0.5, risk="High"), item("nblocked", hours=0.5, status="blocked", blocked="t"),
                   item("nnot", hours=0.5, approved=False)])
    run("--background")
    check("--now: the background run still waits for the delay", installed() == [])
    run("--now", "--no-pull")
    check("--now: installs approved items at once, up to the daily limit",
          installed() == ["x/n1", "x/n2"])
    check("--now: never High risk, blocked or unapproved items", not {"x/nhigh", "x/nblocked", "x/nnot"} & set(installed()))
    rows = sync.history_read()
    check("--now: recorded in the history as automatic, so undo and the daily limit count it",
          [r["key"] for r in rows] == ["x/n1", "x/n2"] and all(r["auto"] and r["now"] for r in rows))
    calls.clear()
    run("--now", "--no-pull")
    check("--now: a second run on the same day stays within the daily limit", installed() == [])
    sync.input = lambda prompt="": "y"
    run("--undo")
    check("--now: undo removes what it installed", sorted(calls) == [("remove", "x/n1"), ("remove", "x/n2")])

    world("mode: auto\nauto_wait_hours: 6\n", [item("p1", hours=0.5)])
    run("--pause")
    run("--now", "--no-pull")
    check("--now: does nothing while paused", installed() == [])
    run("--resume")
    run("--now", "--dry-run", "--no-pull")
    check("--now --dry-run: records nothing as installed", not sync.load_json(sync.STATE_PATH, {})
          and not sync.history_read())


def test_self_update() -> None:
    """The plugin updates itself when the reviews come from a newer star-triage: at most once a day."""
    started = []
    real = sync.start_background
    sync.start_background = lambda flag="--background": started.append(flag)
    world("mode: manual\n", [])
    old = "star-triage on this computer is 5.4.1, your reviews use 9.9.9"
    try:
        t = 1_000_000.0
        line = sync.self_update_message(old, now=t, allowed=False)
        check("self-update: off (auto_update false, or not the plugin): the update commands instead",
              started == [] and line.startswith(old) and "claude plugin update star-triage@star-triage" in line)
        line = sync.self_update_message(old, now=t, allowed=True)
        check("self-update: starts the background update and says to restart",
              started == ["--self-update"]
              and line == "updating star-triage to 9.9.9 in the background; restart Claude Code to use it")
        line = sync.self_update_message(old, now=t + 3600, allowed=True)
        check("self-update: not again within a day", started == ["--self-update"] and "updating star-triage" in line)
        sync.self_update_message(old, now=t + 25 * 3600, allowed=True)
        check("self-update: tries again after a day", started == ["--self-update"] * 2)
        last = sync.load_json(sync.SELF_UPDATE_PATH, {})
        sync.save_json(sync.SELF_UPDATE_PATH, {**last, "done": True, "ok": True, "shown": False})
        line = sync.self_update_message(old, now=t + 26 * 3600, allowed=True)
        check("self-update: done, this session is still old: 'updated star-triage to X; restart'",
              line == "updated star-triage to 9.9.9; restart Claude Code to use it" and len(started) == 2)
        sync.save_json(sync.SELF_UPDATE_PATH, {**last, "done": True, "ok": True, "shown": False})
        check("self-update: done and loaded: said once", sync.self_update_message(None, now=t) == "updated star-triage to 9.9.9."
              and sync.self_update_message(None, now=t) == "")
        sync.save_json(sync.SELF_UPDATE_PATH, {**last, "done": True, "ok": False, "shown": False})
        line = sync.self_update_message(old, now=t + 26 * 3600, allowed=True)
        check("self-update: a failure is shown once, with the commands to run by hand",
              "could not update itself" in line and "claude plugin marketplace update star-triage" in line)
        check("self-update: auto_update false in config.yml is the off switch",
              paths.config_on(paths.read_simple_config(DATA / "config.yml"), "auto_update")
              and not paths.config_on({"auto_update": "false"}, "auto_update"))
    finally:
        sync.start_background = real

    settings = CLAUDE / "settings.json"
    entry = {"source": {"source": "git", "url": "https://github.com/thebeekeper/star-triage.git"}}
    settings.write_text(json.dumps({"model": "x", "extraKnownMarketplaces": {"star-triage": entry}}), encoding="utf-8")
    check("marketplace auto-update: turned on in the documented settings key, with a backup",
          sync.marketplace_auto_update(True) == "changed"
          and json.loads(settings.read_text(encoding="utf-8"))["extraKnownMarketplaces"]["star-triage"]["autoUpdate"] is True
          and json.loads(settings.read_text(encoding="utf-8"))["model"] == "x"
          and (CLAUDE / "settings.json.bak").exists())
    check("marketplace auto-update: a second time changes nothing", sync.marketplace_auto_update(True) == "already")
    check("marketplace auto-update: auto_update false turns it off again", sync.marketplace_auto_update(False) == "changed"
          and json.loads(settings.read_text(encoding="utf-8"))["extraKnownMarketplaces"]["star-triage"]["autoUpdate"] is False)
    settings.write_text(json.dumps({"extraKnownMarketplaces": {}}), encoding="utf-8")
    check("marketplace auto-update: a marketplace settings don't declare is left alone",
          sync.marketplace_auto_update(True) == "not-declared"
          and settings.read_text(encoding="utf-8") == json.dumps({"extraKnownMarketplaces": {}}))
    settings.write_text("{not json", encoding="utf-8")
    check("marketplace auto-update: a broken settings.json is not touched",
          sync.marketplace_auto_update(True) == "unreadable" and settings.read_text(encoding="utf-8") == "{not json")
    settings.unlink()


try:
    test_mapping()
    test_wait_cap_pause()
    test_history_undo()
    test_hook_message()
    test_now()
    test_self_update()
    test_mode_command()
finally:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
