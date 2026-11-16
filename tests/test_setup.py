#!/usr/bin/env python3
"""Setup and doctor checks from the November 2026 new-user test. Offline: a temp data repo with
a local bare "origin", and tests/fake_gh.py instead of gh. Run: python tests/test_setup.py

  UT-3   doctor before setup: "Not set up yet", the one next step, exit 0, no [FAIL]
  UT-4   the stable launcher at ~/.claude/star-triage.py works from any folder
  UT-5   changing whose stars an existing repo reads gets a warning
  UT-8   the new-repo plan lists every step in one place
  UT-9   setup waits for GitHub Actions to register the workflow, pushes a change to the
         workflow file when it doesn't, and never prints a raw HTTP 404; doctor gives --repair
  UT-11  a new repo starts on main even when git's default is master
  UT-5/7/8 the main flow on an existing repo: confirm the repo, one plan, then no questions
  UT-16  doctor notices when the reviews were written by a newer engine than this plugin
  5.6    the schedule: a valid cron for each check interval, config.yml round trip, the doctor's
         mismatch warning, `schedule` commits and pushes, setup's --check-every/--install-wait,
         the plan's timers and first-run lines (UT-19, UT-21), the merge rule per mode (UT-22),
         and the token command naming the account and repo before anything (UT-20)
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="star-triage-setup-")).resolve()
DATA, ORIGIN, CLAUDE = TMP / "data", TMP / "origin.git", TMP / "home" / ".claude"
GITCONFIG = TMP / "gitconfig"
GITCONFIG.write_text("[init]\n\tdefaultBranch = master\n[user]\n\tname = t\n\temail = t@t\n", encoding="utf-8")
os.environ.update(STAR_TRIAGE_DATA=str(DATA), STAR_TRIAGE_GH=str(ENGINE / "tests" / "fake_gh.py"),
                  STAR_TRIAGE_NO_GH="1", FAKE_GH_DIR=str(TMP), FAKE_GH_LOG=str(TMP / "calls.log"),
                  CLAUDE_CONFIG_DIR=str(CLAUDE), HOME=str(TMP / "home"), USERPROFILE=str(TMP / "home"),
                  GIT_CONFIG_GLOBAL=str(GITCONFIG), GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
for key in ("CLAUDE_PLUGIN_ROOT", "FAKE_WF_AFTER", "FAKE_RUN", "STAR_TRIAGE_SETUP_STAGE"):
    os.environ.pop(key, None)
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"\n     {detail}" if detail and not ok else ""))
    failed += 0 if ok else 1


def git(*args: str, cwd: Path = DATA) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def captured(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args)
    return result, buf.getvalue()


def make_data_repo() -> None:
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(ORIGIN)], check=True)
    shutil.copytree(ENGINE / "template", DATA)
    git("init", "-q", "-b", "main")
    # Looks like GitHub to setup and doctor; pushes go to the local bare repo.
    git("remote", "add", "origin", "https://github.com/o/r.git")
    git("remote", "set-url", "--push", "origin", str(ORIGIN))
    git("add", "-A")
    git("commit", "-qm", "Start star-triage")
    git("push", "-qu", "origin", "main")


def wf_reset(after: str) -> None:
    os.environ["FAKE_WF_AFTER"] = after
    (TMP / "wf_calls").unlink(missing_ok=True)


def run_py(args: list, cwd: Path, **env) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in ("STAR_TRIAGE_DATA", "STAR_TRIAGE_GH")}
    full.update(env)
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=full, capture_output=True,
                          text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=120)


make_data_repo()
sys.path.insert(0, str(ENGINE / "scripts"))
import doctor  # noqa: E402
import paths  # noqa: E402
import schedule as sched  # noqa: E402
import setup  # noqa: E402

setup.time.sleep = lambda seconds: None  # the polling waits are not needed against a fake


def test_doctor_before_setup() -> None:
    empty = TMP / "elsewhere"
    empty.mkdir()
    res = run_py([ENGINE / "scripts" / "doctor.py"], empty)
    check("UT-3 doctor before setup exits 0", res.returncode == 0, res.stdout[-300:] + res.stderr[-300:])
    check("UT-3 it says 'Not set up yet' with one next step, and no [FAIL]",
          "Not set up yet" in res.stdout and "[FAIL]" not in res.stdout and 'star-triage.py" setup' in res.stdout
          and "to fix most of them" not in res.stdout, res.stdout[-400:])


def test_launcher() -> None:
    res = run_py([ENGINE / "scripts" / "paths.py", "launcher"], TMP)
    launcher = CLAUDE / "star-triage.py"
    check("UT-4 `paths.py launcher` writes ~/.claude/star-triage.py and prints a short command",
          res.returncode == 0 and launcher.read_bytes() == (ENGINE / "template" / "star-triage.py").read_bytes()
          and res.stdout.strip().endswith('star-triage.py" setup') and "cache" not in res.stdout,
          res.stdout + res.stderr[-300:])
    res = run_py([launcher, "doctor"], TMP / "elsewhere", STAR_TRIAGE_ENGINE=str(ENGINE))
    check("UT-4 the launcher in ~/.claude runs the engine from another folder (and doesn't treat "
          "~/.claude as a data repo)", res.returncode == 0 and "Not set up yet" in res.stdout,
          res.stdout[-300:] + res.stderr[-300:])


def test_questions_and_plan() -> None:
    check("UT-5 changing github_user on an existing repo is named",
          setup.user_change_warning("", "someone", "me") == "This repo will start reading someone's stars instead of me's."
          and setup.user_change_warning("me", "ME", "me") is None and setup.user_change_warning("", "", "me") is None)
    plan = setup.new_repo_plan("me", "my-stars", TMP / "my-stars", "oauth")
    check("UT-8 one plan: create on main, settings, token at a hidden prompt, first run",
          len(plan) == 4 and "me/my-stars" in plan[0] and "main" in plan[0] and "pull requests" in plan[1]
          and "hidden prompt" in plan[2] and "first run" in plan[3])
    text = setup.config_from_flags((ENGINE / "template" / "config.yml").read_text(encoding="utf-8"),
                                   ["--mode", "auto-plus", "--max", "5", "--check-every", "1", "--install-wait", "1"])
    plan = setup.new_repo_plan("me", "my-stars", TMP / "my-stars", "command", text)
    check("UT-21 the plan says why the first run is small, and the later size",
          "3 repos, so you see results in a few minutes; after that 5 per run" in plan[3], plan[3])
    check("UT-19 the plan names both timers with their numbers, and how to change them",
          len(plan) == 5 and "checks for new stars every hour" in plan[4]
          and "auto-plus mode a reviewed tool installs by itself 1 h after its review" in plan[4]
          and "star-triage.py schedule" in plan[4], plan[-1])


def test_start_on_main() -> None:
    fresh = TMP / "fresh"
    fresh.mkdir()
    git("init", "-q", cwd=fresh)
    before = git("symbolic-ref", "--short", "HEAD", cwd=fresh)
    setup.start_on_main(fresh)
    check("UT-11 a new repo starts on main although git's default here is master",
          before == "master" and git("symbolic-ref", "--short", "HEAD", cwd=fresh) == "main", before)


def test_registration() -> None:
    wf_reset("13")  # missed by the first push: shows up only after the nudge
    wf = DATA / setup.WORKFLOW_PATH
    old = wf.read_bytes()
    ok, out = captured(setup.ensure_registered, "o/r", DATA)
    check("UT-9 not registered after the first push: setup pushes a change to the workflow file",
          git("log", "-1", "--format=%s", "main", cwd=ORIGIN) == "Register the star-triage workflow with GitHub Actions"
          and wf.read_bytes().rstrip() == old.rstrip() and wf.read_bytes() != old, out[-300:])
    check("UT-9 ... and then sees it registered", ok is True, out[-300:])

    wf_reset("999")  # never shows up
    ok, out = captured(setup.ensure_registered, "o/r", DATA)
    check("UT-9 still missing: the Actions URL and 'Run workflow', no raw HTTP error",
          ok is False and "https://github.com/o/r/actions" in out and "Run workflow" in out and "404" not in out,
          out[-400:])

    wf_reset("0")
    os.environ["FAKE_RUN"] = "fail"
    _, out = captured(setup.first_run, "o/r", DATA)
    os.environ.pop("FAKE_RUN")
    check("UT-9 a failed `gh workflow run` prints the Actions URL, never the HTTP 404",
          "Could not start the first run" in out and "https://github.com/o/r/actions" in out and "404" not in out,
          out[-400:])
    _, out = captured(setup.first_run, "o/r", DATA)
    check("UT-9 a registered workflow starts at once", "Started." in out, out[-300:])

    wf_reset("999")
    results, _ = captured(doctor.check_github, "o/r", "")
    hit = [r for r in results if "hasn't registered" in r.label]
    check("UT-9 doctor: on GitHub but not registered, and the fix is setup --repair",
          len(hit) == 1 and hit[0].status == "fail" and "setup --repair" in hit[0].fix,
          "; ".join(f"{r.status} {r.label}" for r in results))


def test_engine_version() -> None:
    mine = (ENGINE / "VERSION").read_text(encoding="utf-8").strip()
    installs = DATA / "installs.json"
    installs.write_text(json.dumps({"engine_version": "99.0.0", "items": {}}), encoding="utf-8")
    line = doctor.engine_mismatch(DATA)
    check("UT-16 an older plugin is named with both versions",
          line == f"star-triage on this computer is {mine}, your reviews use 99.0.0", str(line))
    check("UT-16 ... and the update commands are the exact ones",
          "claude plugin marketplace update star-triage" in doctor.UPDATE_HOW
          and "claude plugin update star-triage@star-triage" in doctor.UPDATE_HOW)
    installs.write_text(json.dumps({"engine_version": mine, "items": {}}), encoding="utf-8")
    check("UT-16 the same version, or none recorded, says nothing", doctor.engine_mismatch(DATA) is None)
    installs.unlink()


def test_main_flow() -> None:
    """An existing data repo, start to end, with scripted answers."""
    answers = {"Set up o/r": "y", "Whose stars": "", "Change it?": "", "  > ": "", "Repos to research": "3",
               "Choose 1, 2 or 3": "", "Choose 1 or 2": "1", "Go ahead?": "y"}
    asked, after_go = [], []

    def fake_input(prompt=""):
        (after_go if "Go ahead?" in asked else asked).append(prompt)
        return next((v for k, v in answers.items() if k in prompt), "")

    class Tty(io.StringIO):
        def isatty(self):
            return True

    wf_reset("0")
    os.environ["FAKE_PR_PERM"] = "off"
    setup.input, setup.find_claude, doctor.check_version = fake_input, (lambda: None), (lambda: [])
    setup.real_terminal = lambda: True  # stdout is captured below, so it is not a tty here
    stdin, sys.stdin, sys.argv = sys.stdin, Tty(), ["setup.py"]
    try:
        _, out = captured(setup.main)
    finally:
        sys.stdin = stdin
        os.environ.pop("FAKE_PR_PERM")
    joined = "".join(asked)
    check("UT-5 an existing repo is confirmed first, with the GitHub account",
          asked and asked[0].startswith("Set up o/r (logged in as tester)?") and "[y/N]" in asked[0], str(asked[:1]))
    check("UT-5 reading someone else's stars is warned about, and kept when not confirmed",
          "start reading tester's stars instead of o's" in joined
          and "github_user" not in (DATA / "config.yml").read_text(encoding="utf-8").split("contexts")[0]
          .replace('github_user: ""', ""), joined[-300:])
    check("UT-8 one plan, then nothing else is asked", "Here is what setup will do:" in out and after_go == [],
          str(after_go))
    check("UT-8 the plan's config change is committed and pushed",
          git("log", "-1", "--format=%s", "main", cwd=ORIGIN) == "Configure star-triage"
          and "max_per_run: 3" in git("show", "main:config.yml", cwd=ORIGIN), out[-300:])
    check("UT-7 a GitHub setting says what, why and where (click path), then applies it",
          "Let the workflow open pull requests" in out and "  Why:" in out
          and "Settings > Actions > General > Workflow permissions" in out and "  Done." in out, out[-600:])
    check("UT-9 the first run starts after the workflow is registered", "Started." in out, out[-300:])


def test_flag_helpers() -> None:
    """5.5: setup's answers as flags, for /star-triage:setup."""
    check("flags: good answers have no problems",
          setup.flag_problems(["--repo", "o/r", "--mode", "auto-plus", "--max", "3", "--no-obsidian"]) == [])
    problems = setup.flag_problems(["--mode", "turbo", "--max", "40", "--repo", "x", "--new-repo", "a b",
                                    "--obsidian", "--no-obsidian"])
    check("flags: a bad mode, max, repo and name, and both repo flags or both Obsidian flags, are named",
          len(problems) == 6 and any("--mode" in p for p in problems) and any("1 to 25" in p for p in problems),
          str(problems))
    original = (ENGINE / "template" / "config.yml").read_text(encoding="utf-8")
    text = setup.config_from_flags(original, ["--mode", "auto-plus", "--max", "5", "--no-obsidian", "--no-auto-update"])
    check("flags: mode, max_per_run, obsidian and auto_update are set, everything else kept",
          "\nmode: auto-plus\n" in text and "\nmax_per_run: 5\n" in text and "\nobsidian: false\n" in text
          and "\nauto_update: false\n" in text and text.count("mode: ") == 1
          and len(text.splitlines()) == len(original.splitlines()), text[:400])
    old = "mode: manual\nmax_per_run: 8\ncontexts:\n  - name: General\n"
    added = setup.config_from_flags(old, ["--obsidian"])
    check("flags: a config without `obsidian` gets it, with its comment, above contexts",
          added.index("obsidian: true") < added.index("contexts:") and "# Do you read the notes in Obsidian?" in added,
          added)
    check("flags: no answer flags leave config.yml as it was", setup.config_from_flags(original, ["--yes"]) == original)
    new = (ENGINE / "template" / "config.yml").read_text(encoding="utf-8")
    check("new configs: 1 h safety delay, Obsidian and plugin updates on, manual mode",
          "\nauto_wait_hours: 1\n" in new and "\nobsidian: true\n" in new and "\nauto_update: true\n" in new
          and "\nmode: manual\n" in new)
    check("old configs: no auto_wait_hours line still means 6 h", __import__("paths").wait_hours({}) == 6)


def test_flag_flow() -> None:
    """The /star-triage:setup path, end to end against the fake gh: plan only, then --yes, then
    the token step left to the user's terminal, then the first run once the secret exists."""
    script, log = ENGINE / "scripts" / "setup.py", TMP / "calls.log"
    fake = {"STAR_TRIAGE_GH": str(ENGINE / "tests" / "fake_gh.py")}
    wf_reset("0")
    head = git("rev-parse", "main", cwd=ORIGIN)
    before = (DATA / "config.yml").read_text(encoding="utf-8")
    res = run_py([script, "--repo", "o/r", "--mode", "auto-plus", "--max", "5", "--no-obsidian"], DATA, **fake)
    check("setup flags without --yes: prints the plan, changes nothing, exits 0",
          res.returncode == 0 and "Here is what setup will do:" in res.stdout and "run the same command again with --yes"
          in res.stdout and (DATA / "config.yml").read_text(encoding="utf-8") == before
          and git("rev-parse", "main", cwd=ORIGIN) == head, res.stdout[-600:] + res.stderr[-300:])
    check("setup flags: the plan names the account and the one terminal step for the token",
          "as GitHub account tester" in res.stdout and "one short command in your own terminal" in res.stdout,
          res.stdout[-600:])

    log.write_text("", encoding="utf-8")
    res = run_py([script, "--repo", "o/r", "--mode", "auto-plus", "--max", "5", "--no-obsidian", "--yes"], DATA, **fake)
    shown = git("show", "main:config.yml", cwd=ORIGIN)
    calls = log.read_text(encoding="utf-8")
    check("setup --yes: config.yml gets the answers and is pushed",
          res.returncode == 0 and "\nmode: auto-plus\n" in shown and "\nmax_per_run: 5\n" in shown
          and "\nobsidian: false\n" in shown, res.stdout[-600:] + res.stderr[-300:])
    check("setup --yes: no token is touched; the user gets the one `token` command; no first run yet",
          "secret set" not in calls and 'star-triage.py" token' in res.stdout
          and "first run starts once the token is stored" in res.stdout and "workflow run" not in calls,
          res.stdout[-800:])
    check("setup --no-obsidian: no Obsidian tips, the list command instead",
          "Obsidian 1.9" not in res.stdout and "/star-triage:list" in res.stdout, res.stdout[-500:])

    res = run_py([script, "--repo", "o/r", "--yes"], DATA, FAKE_SECRET="1", **fake)
    check("setup --yes again with the secret stored: starts the first run, config unchanged",
          res.returncode == 0 and "Started." in res.stdout and "already set: CLAUDE_CODE_OAUTH_TOKEN" in res.stdout
          and git("show", "main:config.yml", cwd=ORIGIN) == shown, res.stdout[-600:] + res.stderr[-300:])
    check("UT-21/22 the first-run message says why it is small and the merge rule for the mode",
          "see results in a few minutes; after that 5 per run" in res.stdout
          and "In auto-plus mode: it merges itself unless it approves an install above Medium risk" in res.stdout,
          res.stdout[-1200:])

    res = run_py([script, "--new-repo", "my-stars", "--folder", TMP / "newhome", "--mode", "manual"], DATA, **fake)
    check("setup --new-repo without --yes: the plan only, nothing created",
          res.returncode == 0 and "create the private repo tester/my-stars" in res.stdout
          and not (TMP / "newhome").exists() and "repo create" not in log.read_text(encoding="utf-8"),
          res.stdout[-500:] + res.stderr[-300:])
    res = run_py([script, "--mode", "turbo"], DATA, **fake)
    check("setup: a bad flag is explained, exit 2", res.returncode == 2 and "--mode must be one of" in res.stdout,
          res.stdout[-300:])

    log.write_text("", encoding="utf-8")
    res = run_py([script, "--token"], DATA, **fake)
    check("token: refuses without a real terminal (so it can't run inside Claude), and touches no secret",
          res.returncode == 1 and "only runs in your own terminal" in res.stdout
          and 'star-triage.py" token' in res.stdout and "secret" not in log.read_text(encoding="utf-8"),
          res.stdout[-400:])


def test_schedule() -> None:
    """5.6: the check interval (workflow cron + config.yml) and the install wait."""
    want = {1: "17 * * * *", 3: "17 */3 * * *", 6: "17 */6 * * *", 12: "17 */12 * * *", 24: "17 0 * * *"}
    check("schedule: each choice gives a valid cron at minute 17, read back as the same interval",
          all(sched.cron_for(h) == c and sched.hours_from_cron(c) == h for h, c in want.items()))
    check("schedule: crons that aren't a simple interval are not guessed",
          sched.hours_from_cron("0 9 * * 1") is None and sched.hours_from_cron("*/5 * * * *") is None)
    wf = (ENGINE / "template" / ".github" / "workflows" / "star-triage.yml").read_text(encoding="utf-8")
    daily = sched.set_cron_text(wf, 24)
    check("schedule: the workflow's cron line and its comment change, nothing else",
          '- cron: "17 0 * * *"' in daily and "# Once a day (UTC)." in daily
          and len(daily.splitlines()) == len(wf.splitlines()) and sched.set_cron_text(daily, 3) == wf, daily[:400])

    template = (ENGINE / "template" / "config.yml").read_text(encoding="utf-8")
    text = sched.set_wait_text(sched.set_check_text(template, 12), 0)
    cfg_path = TMP / "cfg-check.yml"
    cfg_path.write_text(text, encoding="utf-8")
    check("schedule: config.yml round trip (check_every_hours 12, auto_wait_hours 0 = no wait) stays valid",
          sched.number_in(text, "check_every_hours") == 12 and paths.wait_hours(paths.parse_simple_config(text)) == 0
          and len(text.splitlines()) == len(template.splitlines())
          and not [r for r in doctor.check_config(cfg_path) if r.status == "fail"])
    old = "mode: auto\nmax_per_run: 8\nmodel: sonnet\ncontexts:\n  - name: General\n"
    added = sched.set_wait_text(sched.set_check_text(old, 6), 1)
    check("schedule: an older config gets both keys, with their comments",
          "\ncheck_every_hours: 6\n" in added and "\nauto_wait_hours: 1\n" in added
          and added.index("check_every_hours") < added.index("model:") and "star-triage.py schedule" in added, added)
    cfg_path.write_text(template.replace("check_every_hours: 3", "check_every_hours: 5"), encoding="utf-8")
    check("doctor: check_every_hours outside 1, 3, 6, 12, 24 fails",
          any(r.status == "fail" and "check_every_hours" in r.label for r in doctor.check_config(cfg_path)))

    config = DATA / "config.yml"
    before = config.read_text(encoding="utf-8")
    config.write_text(sched.set_check_text(before, 1), encoding="utf-8")
    results = doctor.check_schedule()
    check("doctor: warns when config.yml and the workflow's cron disagree, and says how to fix it",
          any(r.status == "warn" and "check_every_hours: 1" in r.label and "every 3 hours" in r.label
              and "schedule --check-every" in r.fix for r in results), str(results))
    config.write_text(before, encoding="utf-8")

    script = ENGINE / "scripts" / "schedule.py"
    res = run_py([script, "--check-every", "2"], DATA)
    check("schedule: a bad value is explained, exit 2, nothing changed",
          res.returncode == 2 and "--check-every must be 1, 3, 6, 12 or 24" in res.stdout
          and config.read_text(encoding="utf-8") == before, res.stdout + res.stderr[-300:])
    res = run_py([script, "--check-every", "1", "--install-wait", "0"], DATA)
    pushed_wf = git("show", "main:.github/workflows/star-triage.yml", cwd=ORIGIN)
    pushed_cfg = git("show", "main:config.yml", cwd=ORIGIN)
    check("schedule --check-every 1 --install-wait 0: workflow cron and config.yml committed and pushed",
          res.returncode == 0 and '- cron: "17 * * * *"' in pushed_wf and "\ncheck_every_hours: 1\n" in pushed_cfg
          and "\nauto_wait_hours: 0\n" in pushed_cfg
          and git("log", "-1", "--format=%s", "main", cwd=ORIGIN).startswith("star-triage schedule: check every hour"),
          res.stdout + res.stderr[-400:])
    check("schedule: it then shows both timers with their numbers",
          "checks for new stars every hour" in res.stdout and "Installs:" in res.stdout, res.stdout)
    res = run_py([script], DATA)
    check("schedule with no option (no terminal): shows both and how to change them, changes nothing",
          res.returncode == 0 and "every hour" in res.stdout and "--check-every" in res.stdout
          and git("status", "--porcelain") == "", res.stdout + res.stderr[-300:])

    check("setup flags: --check-every and --install-wait are checked",
          any("--check-every" in p for p in setup.flag_problems(["--check-every", "5"]))
          and any("--install-wait" in p for p in setup.flag_problems(["--install-wait", "-1"]))
          and setup.flag_problems(["--check-every", "24", "--install-wait", "6"]) == [])
    fake = {"STAR_TRIAGE_GH": str(ENGINE / "tests" / "fake_gh.py")}
    res = run_py([ENGINE / "scripts" / "setup.py", "--repo", "o/r", "--check-every", "6"], DATA, **fake)
    check("setup --check-every without --yes: the plan names the new schedule, nothing changed",
          res.returncode == 0 and "check for new stars every 6 hours" in res.stdout
          and "timers: GitHub checks for new stars every 6 hours" in res.stdout
          and '"17 * * * *"' in (DATA / ".github/workflows/star-triage.yml").read_text(encoding="utf-8"),
          res.stdout[-800:] + res.stderr[-300:])
    res = run_py([ENGINE / "scripts" / "setup.py", "--repo", "o/r", "--check-every", "6", "--yes"], DATA, **fake)
    check("setup --check-every 6 --yes: the workflow's cron and config.yml are pushed together",
          res.returncode == 0 and '- cron: "17 */6 * * *"' in git("show", "main:.github/workflows/star-triage.yml", cwd=ORIGIN)
          and "\ncheck_every_hours: 6\n" in git("show", "main:config.yml", cwd=ORIGIN)
          and "Reviews:  GitHub checks for new stars every 6 hours" in res.stdout, res.stdout[-800:] + res.stderr[-300:])

    check("UT-22 the merge rule is stated for each mode",
          "only adds notes" in paths.merge_rule({"mode": "manual"})
          and "above Low risk" in paths.merge_rule({"mode": "auto"})
          and "above Medium risk" in paths.merge_rule({"mode": "auto-plus"})
          and "always waits" in paths.merge_rule({"auto_merge": "never"}))

    log = TMP / "calls.log"
    log.write_text("", encoding="utf-8")
    setup.real_terminal, setup.input = (lambda: True), (lambda prompt="": "n")
    code, out = captured(setup.token_main)
    check("UT-20 token: names the GitHub account and the repo, and stops on 'no' before any token step",
          code == 1 and "GitHub account: tester" in out and "Secret goes to: o/r" in out and "belongs to o" in out
          and "gh auth switch" in out and "secret set" not in log.read_text(encoding="utf-8"), out)


try:
    test_doctor_before_setup()
    test_launcher()
    test_questions_and_plan()
    test_start_on_main()
    test_registration()
    test_engine_version()
    test_flag_helpers()
    test_main_flow()  # it links the data repo, as real setup does
    test_flag_flow()
    test_schedule()
finally:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
