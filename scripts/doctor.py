"""Check that star-triage is set up correctly, and say exactly how to fix what is not.

    python star-triage.py doctor        on your computer: config, git, GitHub settings, Claude
    python star-triage.py doctor --ci   inside the workflow: config and secrets only

Only the Python standard library is needed to start; PyYAML is checked like
everything else. Nothing here changes any setting: setup.py does that, with your OK.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schedule as sched  # noqa: E402
from paths import (AUTO_MERGE_LOW_RISK, AUTO_MERGE_NEVER, DEFAULT_MAX_PER_RUN, ENGINE_DIR,  # noqa: E402
                   MODES, MODE_SHORT, REPO_URL, TROUBLESHOOTING_URL, daily_limit, find_data_root,
                   launcher_command, read_mode, read_simple_config, running_as_plugin, safe_console,
                   wait_hours)

ROOT = find_data_root() or Path.cwd()  # your data repo (see paths.py)
CONFIG_PATH = ROOT / "config.yml"
WORKFLOW_FILE = "star-triage.yml"
REQUIRED_LABELS = ("type", "category", "status", "verdict", "risk", "saves_tokens", "source")
MODELS = ("sonnet", "opus", "haiku")
SCOPES = ("user", "project", "local")
PLACEHOLDERS = ("Replace with a real project",)
TROUBLESHOOTING = TROUBLESHOOTING_URL


@dataclass
class Result:
    status: str  # ok | warn | fail | todo (not set up yet: a next step, not an error)
    label: str
    fix: str = ""


# ----------------------------------------------------------------- helpers

def run(cmd: list[str], cwd: Path = ROOT, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command and capture its output. Never raises for a missing program."""
    fake = os.environ.get("STAR_TRIAGE_GH")  # tests: tests/fake_gh.py stands in for gh
    if cmd[0] == "gh" and fake:
        cmd = [sys.executable, fake, *cmd[1:]] if fake.endswith(".py") else [fake, *cmd[1:]]
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        return subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if candidate.exists():
            return str(candidate)
    return None


def detect_repo(path: Path | None = None) -> str | None:
    """owner/name of the GitHub repo a folder (default: the data repo) belongs to, from its origin remote."""
    res = run(["git", "remote", "get-url", "origin"], cwd=path or ROOT)
    if res.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", res.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gh_installed() -> bool:
    return bool(os.environ.get("STAR_TRIAGE_GH")) or shutil.which("gh") is not None


def gh_ready() -> bool:
    return gh_installed() and run(["gh", "auth", "status"]).returncode == 0


def gh_json(*args: str):
    res = run(["gh", *args])
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return None


def load_yaml(path: Path):
    import yaml  # imported here so the doctor can report a missing PyYAML instead of crashing
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def pyyaml_hint() -> str:
    """How to install PyYAML here, including on Pythons that refuse plain pip (PEP 668)."""
    py = Path(sys.executable).name
    hint = f"{py} -m pip install --user pyyaml"
    if sys.platform.startswith("linux"):
        hint += ("\nIf pip answers 'externally-managed-environment', use your system's package instead:"
                 "\n  Debian, Ubuntu: sudo apt install python3-yaml"
                 "\n  Fedora:         sudo dnf install python3-pyyaml")
    elif sys.platform == "darwin":
        hint += ("\nIf pip answers 'externally-managed-environment' (Homebrew Python):"
                 f"\n  {py} -m pip install --user --break-system-packages pyyaml")
    return hint


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


UPDATE_COMMANDS = ("claude plugin marketplace update star-triage", "claude plugin update star-triage@star-triage")


UPDATE_HOW = f"Update, in a terminal: {UPDATE_COMMANDS[0]}  then  {UPDATE_COMMANDS[1]}  then restart Claude Code."


def engine_mismatch(data_root: Path = ROOT) -> str | None:
    """'star-triage on this computer is X, your reviews use Y' when the plugin here is older than
    the engine that last wrote installs.json (the Action updates itself; the plugin waits for
    you, UT-16). None when they match or either is unknown."""
    try:
        mine = (ENGINE_DIR / "VERSION").read_text(encoding="utf-8").strip()
        theirs = str(json.loads((data_root / "installs.json").read_text(encoding="utf-8")).get("engine_version") or "")
    except (OSError, ValueError, AttributeError):
        return None
    if theirs and version_tuple(theirs) > version_tuple(mine):
        return f"star-triage on this computer is {mine}, your reviews use {theirs}"
    return None


def actions_hint(repo: str) -> str:
    """What to do when the workflow can't be started from here. Never a raw HTTP error."""
    return (f"Open https://github.com/{repo}/actions , click star-triage on the left, then Run workflow. "
            "If star-triage isn't listed, run: python star-triage.py setup --repair")


def workflow_registered(repo: str) -> bool | None:
    """Has GitHub Actions registered .github/workflows/star-triage.yml? None if gh can't tell."""
    data = gh_json("api", f"repos/{repo}/actions/workflows")
    if not isinstance(data, dict):
        return None
    return any(str(w.get("path", "")).endswith(f"/{WORKFLOW_FILE}") for w in data.get("workflows") or [])


def check_version() -> list[Result]:
    """Warn when a newer star-triage has been released. Silent when GitHub can't be reached."""
    import urllib.request
    try:
        mine = (ENGINE_DIR / "VERSION").read_text(encoding="utf-8").strip()
        raw = REPO_URL.replace("github.com", "raw.githubusercontent.com") + "/main/VERSION"
        with urllib.request.urlopen(raw, timeout=5) as resp:
            latest = resp.read(32).decode("ascii", "replace").strip()
    except (OSError, ValueError):
        return []
    if version_tuple(latest) > version_tuple(mine):
        return [Result("warn", f"star-triage {mine} is installed; {latest} is out",
                       UPDATE_HOW)]
    return [Result("ok", f"star-triage {mine}")]


# ----------------------------------------------------------------- checks

def check_config(path: Path = CONFIG_PATH) -> list[Result]:
    if importlib.util.find_spec("yaml") is None:
        return [Result("fail", "PyYAML is not installed",
                       "Run: " + pyyaml_hint())]
    if not path.exists():
        return [Result("fail", "config.yml is missing",
                       "Copy it from the star-triage template, or run python star-triage.py setup.")]
    try:
        cfg = load_yaml(path)
    except Exception as exc:  # yaml.YAMLError, with line numbers in the message
        return [Result("fail", "config.yml is not valid YAML",
                       f"{str(exc).splitlines()[0]}. Check the indentation near that line; "
                       "lists start with '- ' and every level uses 2 spaces.")]
    if not isinstance(cfg, dict):
        return [Result("fail", "config.yml is empty or not a list of settings", "Restore it from the template.")]

    out: list[Result] = []
    bad = lambda label, fix: out.append(Result("fail", label, fix))  # noqa: E731
    warn = lambda label, fix: out.append(Result("warn", label, fix))  # noqa: E731

    user = cfg.get("github_user") or ""
    if not isinstance(user, str) or (user and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user)):
        bad("github_user is not a GitHub username", 'Use your username, e.g. github_user: "octocat", or leave it "".')

    mpr = cfg.get("max_per_run", DEFAULT_MAX_PER_RUN)
    if not isinstance(mpr, int) or not 1 <= mpr <= 25:
        bad("max_per_run must be a whole number from 1 to 25", "e.g. max_per_run: 8")

    if str(cfg.get("model", "sonnet")).lower() not in MODELS:
        bad("model must be sonnet, opus or haiku", "e.g. model: sonnet")

    am = cfg.get("auto_merge", "notes")
    if am is not False and str(am).strip().lower() not in ("notes", *AUTO_MERGE_LOW_RISK, *AUTO_MERGE_NEVER):
        bad("auto_merge must be notes, low-risk or never", "e.g. auto_merge: notes")

    if "mode" in cfg and str(cfg.get("mode") or "").strip().lower() not in MODES:
        bad("mode must be manual, auto or auto-plus", "Pick one with: python star-triage.py mode")
    for key, low, high in (("auto_wait_hours", 0, 168), ("auto_daily_limit", 1, 50)):
        if key in cfg and not (isinstance(cfg[key], int) and not isinstance(cfg[key], bool) and low <= cfg[key] <= high):
            bad(f"{key} must be a whole number from {low} to {high}", f"e.g. {key}: {1 if low == 0 else 5}")

    if "check_every_hours" in cfg and cfg["check_every_hours"] not in sched.CHECK_CHOICES:
        bad("check_every_hours must be 1, 3, 6, 12 or 24", "Set it with: python star-triage.py schedule --check-every 3")

    if str(cfg.get("install_scope", "user")) not in SCOPES:
        bad("install_scope must be user, project or local", "e.g. install_scope: user")

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(cfg.get("marketplace_name", "my-stars"))):
        bad("marketplace_name must be lowercase letters, numbers and dashes", "e.g. marketplace_name: my-stars")

    contexts = cfg.get("contexts")
    if not isinstance(contexts, list) or not contexts:
        bad("contexts needs at least one project", "Add e.g.\n  - name: General\n    description: Useful to know about")
    else:
        names = []
        for i, c in enumerate(contexts, 1):
            if not isinstance(c, dict) or not str(c.get("name") or "").strip():
                bad(f"contexts item {i} has no name", "Each item needs '- name: ...' and '  description: ...'.")
                continue
            names.append(c["name"])
            if any(p in str(c.get("description", "")) for p in PLACEHOLDERS):
                warn(f"contexts still has the example project '{c['name']}'",
                     "Replace it with a real project of yours, or delete it.")
        if len(names) != len(set(names)):
            bad("two contexts have the same name", "Context names must be unique.")

    labels = cfg.get("labels")
    if not isinstance(labels, dict):
        bad("labels is missing", "Restore the labels section from the template.")
    else:
        for key in REQUIRED_LABELS:
            if not isinstance(labels.get(key), list) or not labels[key]:
                bad(f"labels.{key} is missing or empty", "Restore it from the template.")
        need = {"status": "Inbox", "source": "GitHub star", "verdict": "Install"}
        for key, value in need.items():
            if isinstance(labels.get(key), list) and value not in labels[key]:
                bad(f"labels.{key} must include \"{value}\"", "The automation writes that value.")
        if isinstance(labels.get("risk"), list) and labels["risk"] != ["Low", "Medium", "High"]:
            bad("labels.risk must be exactly [Low, Medium, High]", "The security scan sets these values.")
        if isinstance(labels.get("saves_tokens"), list) and any(isinstance(v, bool) for v in labels["saves_tokens"]):
            bad("labels.saves_tokens has an unquoted Yes or No", 'Quote them: ["Yes", Partly, "No", Costs more]')

    if not isinstance(cfg.get("ignore", []), list):
        bad("ignore must be a list", "e.g. ignore: [] or ignore: [owner/name]")

    if not out:
        out.append(Result("ok", "config.yml looks good"))
    if not any(r.status == "fail" for r in out):
        mode = read_mode(cfg)
        extra = f", after a {wait_hours(cfg)} h safety delay, at most {daily_limit(cfg)} a day" if mode != "manual" else ""
        out.append(Result("ok", f"star-triage mode: {mode} ({MODE_SHORT[mode]}{extra})"))
    return out


def check_ci() -> list[Result]:
    results = check_config()
    if os.environ.get("HAS_OAUTH") != "true" and os.environ.get("HAS_APIKEY") != "true":
        repo = os.environ.get("GITHUB_REPOSITORY", "<you>/<repo>")
        results.append(Result(
            "warn", "No Claude credentials secret",
            f"Runs fail as soon as there is a new star. Add CLAUDE_CODE_OAUTH_TOKEN at "
            f"https://github.com/{repo}/settings/secrets/actions/new , or run python star-triage.py setup."))
    return results


def check_local() -> list[Result]:
    out: list[Result] = []
    if sys.version_info < (3, 9):
        out.append(Result("fail", f"Python {sys.version.split()[0]} is too old", "Install Python 3.9 or newer."))
    else:
        out.append(Result("ok", f"Python {sys.version.split()[0]}"))

    if not shutil.which("git"):
        out.append(Result("fail", "git is not installed", "Install it from https://git-scm.com/downloads"))
        return out + check_config()
    if not (ROOT / "config.yml").exists():
        # A brand-new user: that's expected, not an error (UT-3).
        out.append(Result("todo", "Not set up yet: no star-triage data repo on this computer",
                          "Next step: run /star-triage:setup in Claude Code. Or, in a terminal:\n  "
                          + launcher_command("setup")))
        return out
    out.append(Result("ok", f"Data repo: {ROOT}"))
    mismatch = engine_mismatch()
    if mismatch:
        out.append(Result("warn", mismatch, UPDATE_HOW))
    repo = detect_repo()
    if not repo:
        out.append(Result("fail", "Your data repo is not linked to GitHub (no origin remote)",
                          "Run setup, which creates the GitHub repo for you."))
    else:
        out.append(Result("ok", f"GitHub repo: {repo}"))
    out += check_layout()

    email = run(["git", "config", "user.email"]).stdout.strip()
    if not email:
        out.append(Result("warn", "git has no commit email set",
                          "python star-triage.py setup sets your private GitHub noreply address for this repo."))

    out += check_config()
    out += check_schedule()
    if (ROOT / ".star-triage" / "paused").exists():
        out.append(Result("warn", "Automatic installs are paused on this computer",
                          "Nothing installs or is removed by itself until you run: python star-triage.py resume"))

    if not gh_installed():
        out.append(Result("warn", "GitHub CLI (gh) is not installed, so GitHub settings were not checked",
                          "Install it from https://cli.github.com , then run: gh auth login"))
    elif not gh_ready():
        out.append(Result("warn", "GitHub CLI is not logged in, so GitHub settings were not checked",
                          "Run: gh auth login"))
    elif repo:
        out += check_github(repo, email)

    if find_claude():
        out.append(Result("ok", "Claude Code is installed"))
    else:
        out.append(Result("warn", "Claude Code is not on PATH (needed to install plugins and MCP servers)",
                          f"See 'Installing Claude Code' in {TROUBLESHOOTING}"))
    out += check_installers()
    out += check_version()
    return out


def check_schedule() -> list[Result]:
    """How often GitHub checks (the workflow's cron), and whether config.yml says the same."""
    hours, cron = sched.workflow_hours(ROOT)
    if cron is None:
        return []
    out = [Result("ok", f"Schedule: GitHub checks for new stars {sched.check_words(hours)}"
                  + (f' (cron "{cron}", UTC)' if not hours else ""))]
    problem = sched.mismatch(ROOT, read_simple_config(ROOT / "config.yml"))
    if problem:
        out.append(Result("warn", problem[0].upper() + problem[1:],
                          "Set both to one value: python star-triage.py schedule --check-every N "
                          "(1, 3, 6, 12 or 24)"))
    return out


def check_layout() -> list[Result]:
    """The data repo should call the star-triage Action, not carry its own copy of the code."""
    out = []
    wf = ROOT / ".github" / "workflows" / WORKFLOW_FILE
    text = wf.read_text(encoding="utf-8", errors="ignore") if wf.exists() else ""
    if "uses: thebeekeper/star-triage@" in text or "uses: ./" in text:
        out.append(Result("ok", "Workflow uses the star-triage Action (updates arrive by themselves)"))
    elif "scripts/select_new.py" in text:
        out.append(Result("warn", "This repo still has the old all-in-one layout (its own copy of the code)",
                          "Migrate it: see 'Upgrading from v4' in the star-triage README."))
    else:
        out.append(Result("fail", f".github/workflows/{WORKFLOW_FILE} is missing",
                          "Run setup, which adds it."))
    launcher = ROOT / "star-triage.py"
    if not launcher.exists() and "scripts/select_new.py" not in text:
        out.append(Result("warn", "star-triage.py (the command launcher) is missing from your data repo",
                          "Run setup, which adds it."))
    elif launcher.exists() and '"schedule"' not in launcher.read_text(encoding="utf-8", errors="ignore"):
        out.append(Result("warn", "star-triage.py in your data repo is older than the plugin: it lacks "
                                  "some commands (schedule, token, list, approve)",
                          "Run python star-triage.py setup --skip-config: it offers to update the launcher."))
    return out


def check_installers() -> list[Result]:
    """Tools that approved installs need on this computer."""
    windows = os.name == "nt"
    out = []
    if shutil.which("npx") and shutil.which("npm"):
        out.append(Result("ok", "Node.js is installed (for npm-based tools and MCP servers)"))
    else:
        out.append(Result("warn", "Node.js is not installed, so npm-based tools and MCP servers will be skipped",
                          "Install it: " + ("winget install OpenJS.NodeJS.LTS" if windows else "https://nodejs.org")
                          + ", then open a new terminal."))
    if shutil.which("uv") or shutil.which("pipx"):
        out.append(Result("ok", "uv or pipx is installed (for Python-based tools and MCP servers)"))
    else:
        out.append(Result("warn", "uv is not installed, so Python-based tools and MCP servers will be skipped",
                          "Install it: " + ("winget install astral-sh.uv" if windows
                                            else "curl -LsSf https://astral.sh/uv/install.sh | sh")
                          + ", then open a new terminal."))
    try:
        from sync import hook_installed
        on = hook_installed()
    except Exception:
        on = False
    if on:
        how = "the star-triage plugin" if running_as_plugin() else "your settings.json hook"
        out.append(Result("ok", f"Approved installs happen automatically when Claude Code starts ({how})"))
    else:
        out.append(Result("warn", "Approved installs are not automatic yet",
                          "Install the star-triage plugin in Claude Code: /plugin marketplace add "
                          "thebeekeper/star-triage, then /plugin install star-triage@star-triage"))
    return out


def check_github(repo: str, email: str) -> list[Result]:
    out: list[Result] = []
    info = gh_json("api", f"repos/{repo}")
    if info is None:
        return [Result("fail", f"Cannot read {repo} on GitHub", "Check the repo exists and gh is logged in as its owner.")]

    # The workflow has to be on GitHub, not only in this folder, registered by Actions and
    # switched on. GitHub sometimes misses the workflow in a new repo's first push (UT-9).
    workflow = gh_json("api", f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}")
    if workflow is None:
        pushed = gh_json("api", f"repos/{repo}/contents/.github/workflows/{WORKFLOW_FILE}")
        if pushed is not None and workflow_registered(repo) is False:
            out.append(Result("fail", "The workflow is on GitHub, but GitHub Actions hasn't registered it",
                              "Run: python star-triage.py setup --repair  (it pushes a small change to the "
                              f"workflow file, which makes GitHub look again), then start it at "
                              f"https://github.com/{repo}/actions > star-triage > Run workflow."))
        else:
            out.append(Result("fail", f"The workflow (.github/workflows/{WORKFLOW_FILE}) is not on GitHub yet",
                              "Run python star-triage.py setup: it commits and pushes it."))
    elif str(workflow.get("state", "")).startswith("disabled"):
        out.append(Result("fail", "The star-triage workflow is disabled on GitHub",
                          f"Turn it on: gh workflow enable {WORKFLOW_FILE} --repo {repo}"))
    else:
        out.append(Result("ok", "The workflow is on GitHub and enabled"))

    login_info = gh_json("api", "user") or {}
    m = re.fullmatch(r"(\d+)\+([^@]+)@users\.noreply\.github\.com", email or "")
    if m and login_info.get("id") and int(m.group(1)) != int(login_info["id"]):
        out.append(Result("fail", "Your commit email points at a different GitHub account",
                          f"Run: git config user.email {login_info['id']}+{login_info.get('login')}"
                          "@users.noreply.github.com"))

    secrets = gh_json("secret", "list", "--repo", repo, "--json", "name")
    names = {s.get("name") for s in secrets or []}
    if secrets is None:
        out.append(Result("warn", "Could not read this repo's secrets, so the Claude credential was not checked",
                          f"Look for CLAUDE_CODE_OAUTH_TOKEN at https://github.com/{repo}/settings/secrets/actions"))
    elif names & {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}:
        out.append(Result("ok", "Claude credentials secret is set"))
    else:
        out.append(Result("fail", "No Claude credentials secret",
                          "Run python star-triage.py setup, or add CLAUDE_CODE_OAUTH_TOKEN at "
                          f"https://github.com/{repo}/settings/secrets/actions/new"))

    actions = gh_json("api", f"repos/{repo}/actions/permissions") or {}
    if actions and not actions.get("enabled"):
        out.append(Result("fail", "GitHub Actions is turned off for this repo",
                          f"Turn it on at https://github.com/{repo}/settings/actions"))
    perms = gh_json("api", f"repos/{repo}/actions/permissions/workflow")
    if perms is not None:
        if perms.get("can_approve_pull_request_reviews"):
            out.append(Result("ok", "Actions may open pull requests"))
        else:
            out.append(Result("fail", "Actions may not open pull requests",
                              "Run python star-triage.py setup, or tick 'Allow GitHub Actions to create and approve "
                              f"pull requests' at https://github.com/{repo}/settings/actions"))

    runs = gh_json("run", "list", "--repo", repo, "--workflow", WORKFLOW_FILE, "--limit", "1",
                   "--json", "status,conclusion,url,createdAt")
    if workflow is None:
        pass  # reported above
    elif runs == []:
        out.append(Result("warn", "The workflow has never run",
                          f"Start it: gh workflow run {WORKFLOW_FILE} --repo {repo} -f max=3\n"
                          + actions_hint(repo)))
    elif runs:
        last = runs[0]
        if last.get("status") != "completed":
            out.append(Result("ok", f"A run is in progress: {last.get('url')}"))
        elif last.get("conclusion") == "success":
            out.append(Result("ok", f"Last run succeeded ({last.get('createdAt', '')[:10]})"))
        else:
            out.append(Result("fail", f"Last run ended with '{last.get('conclusion')}'",
                              f"Open it for the reason: {last.get('url')}"))
    return out


# ----------------------------------------------------------------- output

def report(results: list[Result], ci: bool = False) -> int:
    fails = [r for r in results if r.status == "fail"]
    if ci:
        for r in results:
            if r.status != "ok":
                kind = "error" if r.status == "fail" else "warning"
                print(f"::{kind} title={r.label}::{r.fix}".replace("\n", " "))
            else:
                print(r.label)
        problems = [r for r in results if r.status != "ok"]
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if problems and summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("### Setup check\n\n| | Problem | How to fix |\n|---|---|---|\n")
                for r in problems:
                    icon = "Error" if r.status == "fail" else "Warning"
                    f.write(f"| {icon} | {r.label} | {r.fix.replace(chr(10), ' ')} |\n")
                f.write("\n")
        return 1 if fails else 0

    marks = {"ok": "[ ok ]", "warn": "[warn]", "fail": "[FAIL]", "todo": "[next]"}
    for r in results:
        print(f"{marks[r.status]} {r.label}")
        if r.fix and r.status != "ok":
            for line in r.fix.splitlines():
                print(f"       {line}")
    print()
    if any(r.status == "todo" for r in results) and not fails:
        print("Not set up yet. Run the command above to start.")
    elif fails:
        print(f"{len(fails)} problem(s) to fix. Run python star-triage.py setup to fix most of them for you.")
    elif any(r.status == "warn" for r in results):
        print("Working, with warnings above.")
    else:
        print("All good.")
    return 1 if fails else 0


def main() -> None:
    safe_console()
    ci = "--ci" in sys.argv[1:]
    sys.exit(report(check_ci() if ci else check_local(), ci=ci))


if __name__ == "__main__":
    main()
