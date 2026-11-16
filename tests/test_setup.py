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


try:
    test_doctor_before_setup()
    test_launcher()
    test_questions_and_plan()
    test_start_on_main()
    test_registration()
    test_engine_version()
    test_main_flow()  # last: it links the data repo, as real setup does
finally:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
