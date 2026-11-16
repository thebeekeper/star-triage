"""Guided setup for star-triage. Safe to run again.

    python "$HOME/.claude/star-triage.py" setup   first time, from any folder (the plugin keeps
                                                  this launcher there; the path never changes)
    python star-triage.py setup                   from your data repo, once it exists
    ... --step-by-step   ask before every single step, as setup did before 5.4
    ... --check          only check, change nothing (same as doctor)
    ... --skip-config    keep config.yml as it is
    ... --repair         push a small change to the workflow file, so GitHub Actions registers it
    ... --migrate        upgrade a v4 copy (with its own scripts/) to the Action + plugin

How it works: a few questions first (which repo, your projects, the star-triage mode, how the
workflow uses Claude), then ONE plan and one "Go ahead?". Then it runs straight through:
  1. Creates your data repo (a private GitHub repo with config.yml, tracker/, a launcher and a
     20-line workflow that calls the star-triage Action), on branch main, and pushes it.
     An existing data repo is used only after you confirm the repo and the GitHub account.
  2. Writes config.yml from your answers and pushes it.
  3. Applies the GitHub settings star-triage needs, printing for each what it is, why, and
     where it lives on GitHub so you can check or undo it.
  4. Stores your Claude token as a repo secret. The token goes from `claude setup-token`
     straight into GitHub's hidden prompt. It is never written to a file or shown to Claude.
  5. Waits until GitHub Actions has registered the workflow, then starts the first run.
Creating the repo and steps 3 to 5 use the GitHub CLI (https://cli.github.com). Without it you
get links instead.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
from doctor import (CONFIG_PATH, ROOT, WORKFLOW_FILE, actions_hint, find_claude, gh_json,  # noqa: E402
                    gh_ready, run, workflow_registered)
from mode import set_mode_text  # noqa: E402
from paths import (DEFAULT_MAX_PER_RUN, ENGINE_DIR, MODES, TROUBLESHOOTING_URL, is_data_repo,  # noqa: E402
                   read_mode, running_as_plugin, safe_console, write_link)

TEMPLATE_DIR = ENGINE_DIR / "template"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
# Files a v4 copy carried itself; the Action and the plugin provide them now.
OLD_ENGINE_PATHS = ["scripts", "prompts", "docs", "LICENSE", ".claude/skills/star-triage",
                    ".claude/skills/star-triage-sync", ".claude/skills/star-triage-setup"]
# Set by setup for the copy of itself it starts inside a data repo it has just made.
STAGE = os.environ.get("STAR_TRIAGE_SETUP_STAGE", "")  # "created" or "approved" (plan said yes)
STEP_BY_STEP = "--step-by-step" in sys.argv[1:]

TOP_KEY = re.compile(r"^[A-Za-z_][\w-]*:")


# ----------------------------------------------------------------- prompts

def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, title: str) -> None:
    say(f"\n== {n}. {title} " + "=" * max(3, 60 - len(title)))


def ask(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{hint}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def yes(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{question} [{hint}] ").strip().lower()
    except EOFError:
        return False  # nobody answered: never treat that as a yes
    return default if not answer else answer.startswith("y")


def confirm(question: str) -> bool:
    """A step the plan already covers: asked only with --step-by-step."""
    return yes(question) if STEP_BY_STEP else True


def ask_lines(prompt: str) -> list[str]:
    say(prompt)
    lines = []
    while True:
        try:
            line = input("  > ").rstrip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return lines


def interactive(cmd: list[str]) -> int:
    """Run a command attached to this terminal, so its own prompts work."""
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        return subprocess.call([exe, *cmd[1:]], cwd=ROOT)
    except OSError as exc:
        say(f"Could not run {cmd[0]}: {exc}")
        return 127


def explain(what: str, why: str, where: str) -> None:
    """Every GitHub setting setup touches: what it is, why star-triage needs it, where it lives."""
    say(f"\n{what}")
    say(f"  Why:   {why}")
    say(f"  Where: {where}")


def offer(what: str, why: str, where: str) -> bool:
    explain(what, why, where)
    return yes("  I'll turn this on for you on GitHub. OK?") if STEP_BY_STEP else True


def show_plan(lines: list[str]) -> bool:
    say("\nHere is what setup will do:")
    for line in lines:
        say(f"  - {line}")
    say("Nothing else is asked after this, except pasting your Claude token at a hidden prompt.")
    return yes("Go ahead?")


def clear_screen() -> None:
    if os.name == "nt":
        subprocess.call("cls", shell=True)
    else:
        subprocess.call(["clear"])
        print("\033[3J", end="", flush=True)  # also the scrollback, where the terminal supports it


# ----------------------------------------------------------------- config.yml editing
# Edits keep your comments: only the lines of the setting being changed are replaced.

def block_span(lines: list[str], key: str) -> tuple[int, int] | None:
    for i, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            return i, j
    return None


def set_setting(text: str, key: str, new_lines: list[str], before: str | None = None) -> str:
    lines = text.splitlines()
    span = block_span(lines, key)
    if span:
        lines[span[0]:span[1]] = new_lines
    else:
        anchor = block_span(lines, before) if before else None
        if anchor:
            # Put it above the anchor's comment lines, if any.
            k = anchor[0]
            while k > 0 and lines[k - 1].startswith("#"):
                k -= 1
            lines[k:k] = new_lines + [""]
        else:
            lines += [""] + new_lines
    return "\n".join(lines) + "\n"


def q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid YAML string


def user_change_warning(current: str, new: str, owner: str) -> str | None:
    """Changing whose stars an existing repo reads gets its own warning (UT-5)."""
    before, after = (current or owner).strip(), (new or owner).strip()
    if before and after and before.lower() != after.lower():
        return f"This repo will start reading {after}'s stars instead of {before}'s."
    return None


def ask_config(text: str, cfg: dict, gh_login: str | None, owner: str, existing: bool) -> str:
    """Ask the config questions and return config.yml's new text (nothing is written here)."""
    say("Press Enter to keep the value in [brackets].")

    current_user = cfg.get("github_user") or ""
    user = ask("Whose stars should be read (GitHub username)", current_user or gh_login or owner or "")
    # Left empty, the workflow reads the stars of the repo's owner, which is usually you.
    if user != current_user and not (not current_user and user.lower() == owner.lower()):
        warning = user_change_warning(current_user, user, owner) if existing else None
        if warning and not yes(f"{warning} Change it?", default=False):
            say("Kept.")
        else:
            text = set_setting(text, "github_user", [f"github_user: {q(user)}"])

    say("\nOptional: your projects. Each becomes a 'relevant_to' label on notes. Every note also")
    say("lists 2 or 3 examples of when you'd use the tool, so you can skip this.")
    names = [str(c.get("name")) for c in cfg.get("contexts") or [] if isinstance(c, dict)]
    if names:
        say("Current: " + ", ".join(names))
    projects = ask_lines("One per line as  Name: one-line description  (empty line to finish, "
                         "or just Enter to keep the current list):")
    if projects:
        block = ["contexts:"]
        seen = set()
        for line in projects:
            name, _, desc = line.partition(":")
            name, desc = name.strip(), desc.strip() or name.strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                block += [f"  - name: {q(name)}", f"    description: {q(desc)}"]
        if "general" not in seen:
            block += ['  - name: "General"', '    description: "Useful to know about, no specific project"']
        text = set_setting(text, "contexts", block)

    mpr = ask("\nRepos to research per run (1-25)", str(cfg.get("max_per_run", DEFAULT_MAX_PER_RUN)))
    if mpr.isdigit() and 1 <= int(mpr) <= 25:
        text = set_setting(text, "max_per_run", [f"max_per_run: {int(mpr)}"])

    say("\nThe star-triage mode: who approves installs? (Not Claude Code's own auto mode.")
    say("Change it later with: python star-triage.py mode)")
    names = list(MODES)
    for n, name in enumerate(names, 1):
        say(f"  {n}) {name + ':':11}{MODES[name]}")
    say("High-risk and blocked items are never installed automatically, in any mode.")
    current = read_mode(cfg)
    pick = ask("Choose 1, 2 or 3", str(names.index(current) + 1))
    chosen = names[int(pick) - 1] if pick in ("1", "2", "3") else current
    # Written only when it is a real choice: keeping the current mode never rewrites an old
    # config that still uses auto_merge.
    if chosen != current or "mode" in cfg:
        text = set_mode_text(text, chosen)
    return text


def save_config(path: Path, original: str, text: str) -> bool:
    """Write config.yml if it changed and is still valid. Returns True if it was written."""
    if text == original:
        say("config.yml unchanged.")
        return False
    path.write_text(text, encoding="utf-8")
    problems = [r for r in doctor.check_config(path) if r.status == "fail"]
    if problems:
        path.write_text(original, encoding="utf-8")
        say("That produced an invalid config.yml, so nothing was changed:")
        for r in problems:
            say(f"  - {r.label}: {r.fix}")
        return False
    say("config.yml updated.")
    return True


def read_config(path: Path) -> tuple[str, dict]:
    text = path.read_text(encoding="utf-8")
    try:
        cfg = doctor.load_yaml(path) or {}
    except Exception:
        cfg = {}
    return text, cfg if isinstance(cfg, dict) else {}


def ask_auth() -> str:
    say("\nHow should the workflow use Claude?")
    say("  1) Your Claude subscription (Pro or Max). Recommended.")
    say("  2) An Anthropic API key (pay per use, from console.anthropic.com).")
    return "apikey" if ask("Choose 1 or 2", "1") == "2" else "oauth"


# ----------------------------------------------------------------- git

def git_in(path: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=path, timeout=timeout)


def start_on_main(path: Path) -> None:
    """A repo with no commits yet starts on `main`, whatever git's own default is (UT-11)."""
    if git_in(path, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        git_in(path, "symbolic-ref", "HEAD", "refs/heads/main")


def ensure_email(login: dict | None, where: Path | None = None) -> None:
    where = where or ROOT
    email = run(["git", "config", "user.email"], cwd=where).stdout.strip()
    if not login or not login.get("id"):
        if not email:
            say("git has no commit email. Set one before committing: see 'Git and commits' in")
            say(f"  {TROUBLESHOOTING_URL}")
        return
    noreply = f"{login['id']}+{login['login']}@users.noreply.github.com"
    m = re.fullmatch(r"(\d+)\+[^@]+@users\.noreply\.github\.com", email)
    if email == noreply:
        say(f"Commit email: {email} (your private GitHub address)")
    elif m and int(m.group(1)) != int(login["id"]):
        say(f"Your commit email {email} belongs to a DIFFERENT GitHub account,")
        say("so your commits would show someone else as the author.")
        if confirm(f"Use {noreply} for this repo?"):
            run(["git", "config", "user.email", noreply], cwd=where)
            say(f"Commit email for this repo: {noreply}")
    elif not email:
        if confirm(f"git has no commit email. Use your private GitHub address {noreply} for this repo?"):
            run(["git", "config", "user.email", noreply], cwd=where)
            say(f"Commit email for this repo: {noreply} (your private GitHub address)")
    else:
        say(f"Commit email: {email}. This is public on GitHub. Your private alternative is {noreply}")
        say(f"  (to switch: git config user.email {noreply})")
    if not run(["git", "config", "user.name"], cwd=where).stdout.strip():
        run(["git", "config", "user.name", login["login"]], cwd=where)


def push(path: Path) -> bool:
    """Push the current branch, and say what to do when GitHub refuses."""
    res = git_in(path, "push", "-u", "origin", "HEAD")
    if res.returncode == 0:
        say("Pushed.")
        return True
    err = res.stderr.strip() or res.stdout.strip()
    say("Push failed:\n" + err)
    low = err.lower()
    if "workflow" in low and "scope" in low:
        say("Your GitHub login may not push workflow files yet. Run: gh auth refresh -s workflow")
    elif any(t in low for t in ("authentication failed", "could not read username", "terminal prompts disabled",
                                "permission denied", "logon failed")):
        say("git is not logged in to GitHub. Run: gh auth setup-git")
    elif any(t in low for t in ("non-fast-forward", "fetch first", "is behind")):
        say("GitHub has commits this folder does not. Run: git pull --rebase")
    say("Then run setup again; it picks up from here.")
    return False


def commit_and_push(path: Path, message: str, files: list[str] | None = None) -> bool:
    """Commit and push. With `files`, only those paths are staged and committed, and they are
    listed first; anything else in the folder is left alone. True when it reached GitHub."""
    only = ["--", *files] if files else []
    if files is None:
        git_in(path, "add", "-A")
    elif files:
        git_in(path, "add", *only)
    else:
        return False
    if not git_in(path, "status", "--porcelain", *only).stdout.strip():
        return False
    if files:
        say("Files to commit:")
        for rel in files:
            say(f"  {rel}")
    if not confirm(f"Commit these changes ('{message}') and push them to GitHub?"):
        say("Skipped. Commit and push them yourself before the next run.")
        return False
    res = git_in(path, "commit", "-q", "-m", message, *only)
    if res.returncode:
        say(res.stderr.strip() or res.stdout.strip())
        return False
    return push(path)


def template_files() -> list[str]:
    return [src.relative_to(TEMPLATE_DIR).as_posix() for src in sorted(TEMPLATE_DIR.rglob("*"))
            if src.is_file() and "__pycache__" not in src.parts]


def unpushed(path: Path) -> list[str]:
    """Starting files and config.yml that differ from the last commit, or were never committed."""
    rels = [rel for rel in template_files() if (path / rel).exists()]
    listed = (git_in(path, "-c", "core.quotePath=false", "ls-files", "--modified", "--others",
                     "--exclude-standard", "--", *rels).stdout
              + git_in(path, "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--", *rels).stdout)
    return sorted({line for line in listed.splitlines() if line})


def commit_settings() -> None:
    """Commit and push config.yml, plus any starting file that never got committed (the first
    commit was declined or failed). Also pushes a commit that never reached GitHub."""
    changed = unpushed(ROOT)
    first = git_in(ROOT, "rev-parse", "--verify", "-q", "HEAD").returncode != 0
    if changed:
        commit_and_push(ROOT, "Start star-triage" if first else "Configure star-triage", changed)
        return
    ahead = git_in(ROOT, "rev-list", "--count", "@{u}..HEAD")
    if not first and (ahead.returncode != 0 or ahead.stdout.strip() != "0"):
        if confirm("Your last commit is not on GitHub yet. Push it now?"):
            push(ROOT)
        else:
            say("Skipped. The workflow reads everything from GitHub, so push before the first run.")
    else:
        say("Nothing to commit: GitHub already has your settings.")


# ----------------------------------------------------------------- GitHub settings

def actions_url(repo: str) -> str:
    return f"https://github.com/{repo}/settings/actions"


def enable_actions(repo: str) -> None:
    actions = gh_json("api", f"repos/{repo}/actions/permissions") or {}
    if actions and not actions.get("enabled"):
        if offer("Turn on GitHub Actions for this repo",
                 "the reviews run as a GitHub Actions workflow in your repo.",
                 f"your repo > Settings > Actions > General > Actions permissions  ({actions_url(repo)})"):
            res = run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions", "-F", "enabled=true"])
            say("  Done." if res.returncode == 0 else "  That did not work. Turn it on at the place above.")
    else:
        say("GitHub Actions is on for this repo.")
    state = (gh_json("api", f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}") or {}).get("state", "")
    if state.startswith("disabled") and offer(
            "Turn the star-triage workflow back on",
            "GitHub switched it off (it does that after 60 days without activity, or someone disabled it).",
            f"your repo > Actions > star-triage > ... > Enable workflow  (https://github.com/{repo}/actions)"):
        res = run(["gh", "workflow", "enable", WORKFLOW_FILE, "--repo", repo])
        say("  Done." if res.returncode == 0 else "  That did not work. Enable it at the place above.")


def allow_pull_requests(repo: str) -> None:
    perms = gh_json("api", f"repos/{repo}/actions/permissions/workflow")
    where = f"your repo > Settings > Actions > General > Workflow permissions  ({actions_url(repo)})"
    if perms is None:
        say(f"Could not read the Actions settings. Set it by hand at {where}: tick 'Allow GitHub Actions")
        say("to create and approve pull requests', then Save.")
        return
    if perms.get("can_approve_pull_request_reviews"):
        say("The workflow may open pull requests (already on).")
        return
    if not offer("Let the workflow open pull requests ('Allow GitHub Actions to create and approve pull requests')",
                 "each run puts its new notes in a pull request, for you to read and merge.", where):
        say("  Skipped. Runs fail at the last step until this is on.")
        return
    res = run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions/workflow",
               "-f", f"default_workflow_permissions={perms.get('default_workflow_permissions', 'read')}",
               "-F", "can_approve_pull_request_reviews=true"])
    if res.returncode == 0:
        say("  Done.")
    else:
        say("  That did not work. Tick it by hand at the place above, then Save.")


def install_hint() -> str:
    if platform.system() == "Windows":
        return "irm https://claude.ai/install.ps1 | iex   (in PowerShell, then open a new window)"
    return "curl -fsSL https://claude.ai/install.sh | bash"


def secret_names(repo: str) -> set[str] | None:
    found = gh_json("secret", "list", "--repo", repo, "--json", "name")
    if found is None:
        return None
    return {s.get("name") for s in found} & {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}


def set_secret(repo: str, how: str | None = None) -> None:
    have = secret_names(repo) or set()
    if have:
        say(f"Claude credentials already set: {', '.join(sorted(have))}.")
        if not STEP_BY_STEP or not yes("Replace it?", default=False):
            if not STEP_BY_STEP:
                say("  (To replace it: run setup with --step-by-step.)")
            return
    how = how or ask_auth()
    where = f"your repo > Settings > Secrets and variables > Actions  (https://github.com/{repo}/settings/secrets/actions)"
    if how == "apikey":
        explain("Store your Anthropic API key as a repo secret (ANTHROPIC_API_KEY)",
                "the workflow uses it to run Claude for the reviews.", where)
        say("\nPaste your API key at the hidden prompt below. It goes straight to GitHub.")
        if interactive(["gh", "secret", "set", "ANTHROPIC_API_KEY", "--repo", repo]) == 0:
            say("Secret saved.")
        else:
            say(f"That did not work. Add ANTHROPIC_API_KEY by hand at {where}")
        return
    claude = find_claude()
    if not claude:
        say("Claude Code is not installed (or not on PATH). Install it, then run this script again:")
        say(f"  {install_hint()}")
        return
    explain("Store your Claude token as a repo secret (CLAUDE_CODE_OAUTH_TOKEN)",
            "the workflow uses it to run Claude for the reviews, on your subscription.", where)
    say("\n" + "!" * 72)
    say("  CAREFUL: the next part shows your Claude token on screen.")
    say("  Don't share, paste into a chat, or screenshot anything from here until setup has")
    say("  cleared the screen. Anyone with the token can use your Claude subscription.")
    say("!" * 72)
    say("`claude setup-token` opens your browser to log in, then prints a long token. Copy it.")
    say("You then paste it at a hidden prompt, which sends it straight to GitHub as a secret.")
    if STEP_BY_STEP and not yes("Ready to start?"):
        say("Skipped. Run setup again when you're ready.")
        return
    if not STEP_BY_STEP:
        ask("Press Enter to start")
    if interactive([claude, "setup-token"]) != 0:
        say("setup-token did not finish. Run setup again when ready.")
        return
    say("\nNow paste the token (input is hidden) and press Enter:")
    saved = interactive(["gh", "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN", "--repo", repo]) == 0
    say("Secret saved." if saved else f"That did not work. Add CLAUDE_CODE_OAUTH_TOKEN by hand at {where}")
    say("Your token is still on screen above.")
    if yes("Clear the screen now?"):
        clear_screen()
        say("Screen cleared. Setup continues.")
    else:
        say("Clear it yourself before you share anything from this window (cls on Windows, clear elsewhere).")
    say("If the token was ever shared by mistake, make a new one and replace the secret: see")
    say(f"'Claude token was shared' in {TROUBLESHOOTING_URL}")


# ----------------------------------------------------------------- first run (UT-9)

def wait_registered(repo: str, seconds: int = 60, every: int = 5) -> bool:
    """Poll GitHub until Actions lists the star-triage workflow."""
    for i in range(max(1, seconds // every)):
        if workflow_registered(repo):
            return True
        if i < seconds // every - 1:
            time.sleep(every)
    return False


def nudge_workflow(path: Path) -> bool:
    """Commit and push a one-character change to the workflow file (a trailing blank line, added
    or taken away). GitHub looks at a workflow again when a push changes it."""
    wf = path / WORKFLOW_PATH
    try:
        text = wf.read_bytes()
    except OSError:
        say(f"{WORKFLOW_PATH} is missing in {path}. Run setup again to add it.")
        return False
    body = text.rstrip(b"\r\n") + b"\n"
    wf.write_bytes(body if text != body else body + b"\n")
    git_in(path, "add", "--", WORKFLOW_PATH)
    res = git_in(path, "commit", "-q", "-m", "Register the star-triage workflow with GitHub Actions",
                 "--", WORKFLOW_PATH)
    if res.returncode:
        say(res.stderr.strip() or res.stdout.strip())
        return False
    return push(path)


def ensure_registered(repo: str, path: Path) -> bool:
    """Make sure GitHub Actions knows the workflow before anything tries to start it."""
    if workflow_registered(repo):
        return True
    say("Waiting for GitHub Actions to pick up the workflow (up to a minute)...")
    if wait_registered(repo):
        say("GitHub Actions has the star-triage workflow.")
        return True
    say("Not there yet. GitHub sometimes misses the workflow in a brand-new repo's first push.")
    say("Pushing a small change to the workflow file, which makes it look again...")
    if nudge_workflow(path) and wait_registered(repo):
        say("GitHub Actions has the star-triage workflow.")
        return True
    say("GitHub still hasn't listed the workflow. " + actions_hint(repo))
    return False


def start_workflow(repo: str, tries: int = 4) -> bool:
    for i in range(tries):
        if run(["gh", "workflow", "run", WORKFLOW_FILE, "--repo", repo, "-f", "max=3"]).returncode == 0:
            return True
        if i < tries - 1:
            time.sleep(10)  # just registered: GitHub can take a few seconds more to accept a run
    return False


def first_run(repo: str, path: Path | None = None) -> None:
    path = path or ROOT
    try:
        per_run = int((doctor.load_yaml(path / "config.yml") or {}).get("max_per_run", DEFAULT_MAX_PER_RUN))
    except Exception:
        per_run = DEFAULT_MAX_PER_RUN
    say("Every repo you have starred so far gets reviewed, newest first. After this first run the")
    say(f"workflow takes {per_run} per run (max_per_run in config.yml), every 3 hours, until it has caught up.")
    say("Each review is a short Claude session. To go slower, lower max_per_run; to leave repos out,")
    say("list them under ignore in config.yml. With no stars yet, star a few repos first.")
    if STEP_BY_STEP and not yes("Start the first run now, with 3 repos?"):
        say(f"Later: {actions_hint(repo)}")
        return
    if not ensure_registered(repo, path):
        return
    if not start_workflow(repo):
        say("Could not start the first run from here. " + actions_hint(repo))
        return
    time.sleep(4)
    runs = gh_json("run", "list", "--repo", repo, "--workflow", WORKFLOW_FILE, "--limit", "1", "--json", "url")
    url = runs[0]["url"] if runs else f"https://github.com/{repo}/actions"
    say(f"Started. Watch it here (takes about 2 to 5 minutes): {url}")
    say("When it has finished:")
    say("  1. Check the run is green. If it is red, the run page says why and how to fix it.")
    say(f"  2. Merge the pull request if one is waiting: https://github.com/{repo}/pulls")
    say("     (whether it waits depends on your star-triage mode).")
    say(f"  3. Get the notes onto this computer: run `git pull` in {path}")


def check_default_branch(repo: str, path: Path) -> None:
    """Say so if the branch setup pushed is not the repo's default (nothing assumes a name)."""
    info = gh_json("repo", "view", repo, "--json", "defaultBranchRef") or {}
    default = (info.get("defaultBranchRef") or {}).get("name")
    mine = git_in(path, "branch", "--show-current").stdout.strip()
    if default and mine and default != mine:
        say(f"Note: GitHub's default branch for {repo} is '{default}', but this folder is on '{mine}'.")
        say("The workflow runs on the default branch. Switch with: git switch " + default)


def automatic_installs() -> None:
    import sync
    say("Approved items (merged PRs, or a ticked `approve` box in Obsidian) install by")
    say("themselves: when Claude Code starts, the star-triage plugin pulls your approvals and")
    say("installs anything new in the background.")
    if running_as_plugin():
        say("On: the star-triage plugin is installed.")
    else:
        say("Install the plugin in Claude Code (once):")
        say("  /plugin marketplace add thebeekeper/star-triage")
        say("  /plugin install star-triage@star-triage")
        if sync.hook_installed():
            say("Until then, your settings.json hook keeps it automatic.")
    say("Install by hand any time with: python star-triage.py sync")
    for r in doctor.check_installers():
        if r.status != "ok" and "automatic" not in r.label:
            say(f"Note: {r.label}. {r.fix}")


# ----------------------------------------------------------------- the data repo

def copy_template(target: Path, overwrite: tuple[str, ...] = ()) -> list[str]:
    """Copy the data-repo skeleton into target. Existing files are kept unless listed."""
    written = []
    for src in sorted(TEMPLATE_DIR.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(TEMPLATE_DIR).as_posix()
        dest = target / rel
        if dest.exists() and rel not in overwrite:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        written.append(rel)
    return written


def new_repo_plan(login: str, name: str, target: Path, auth: str | None) -> list[str]:
    token = ("store your Anthropic API key as a repo secret (you paste it at a hidden prompt)" if auth == "apikey"
             else "store your Claude token as a repo secret (`claude setup-token`, then you paste it at a "
                  "hidden prompt)")
    return [f"create the private repo {login}/{name} in {target}, on branch main, with your settings, "
            "and push it",
            "make sure GitHub Actions is on, and let its workflow open pull requests (repo settings)",
            token,
            "wait until GitHub Actions has registered the workflow, then start the first run (3 repos)"]


def create_data_repo(login: dict | None) -> tuple[Path, str | None] | None:
    """Ask for a name and folder, ask the config questions, show the plan, then create the
    private repo, push it, and return (folder, auth choice)."""
    if not login:
        say("To create your data repo automatically, install the GitHub CLI (https://cli.github.com),")
        say("run `gh auth login`, then run setup again. Or by hand: create an empty private repo on")
        say("GitHub, clone it, and run setup from inside that folder.")
        return None
    say(f"It will belong to the GitHub account you're logged in as: {login['login']}.")
    say("(Another account? Stop here, run `gh auth switch` or `gh auth login`, and start again.)")
    name = ask("Name for your new private GitHub repo", "my-stars")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name):
        say("That is not a valid repo name.")
        return None
    parent = Path(ask("Folder to put it in", str(Path.home() / "Documents"))).expanduser()
    target = parent / name
    if target.exists():
        say(f"{target} already exists. Pick another name, or run setup from inside that folder.")
        return None

    original = (TEMPLATE_DIR / "config.yml").read_text(encoding="utf-8")
    text, auth = original, None
    if STEP_BY_STEP:
        if not yes(f"Create github.com/{login['login']}/{name} (private) and clone it to {target}?"):
            return None
    else:
        say("")
        text = ask_config(original, doctor.load_yaml(TEMPLATE_DIR / "config.yml") or {},
                          login["login"], login["login"], existing=False)
        auth = ask_auth()
        if not show_plan(new_repo_plan(login["login"], name, target, auth)):
            say("Nothing was changed.")
            return None

    parent.mkdir(parents=True, exist_ok=True)
    res = run(["gh", "repo", "create", name, "--private", "--clone",
               "--description", "My GitHub stars, reviewed by star-triage"], cwd=parent, timeout=180)
    if res.returncode or not target.exists():
        say("Could not create it: " + (res.stderr.strip() or res.stdout.strip()))
        return None
    start_on_main(target)
    written = copy_template(target)
    if text != original:
        save_config(target / "config.yml", original, text)
    ensure_email(login, target)
    commit_and_push(target, "Start star-triage", written)
    return target, auth


def confirm_existing(repo: str | None, login: dict | None) -> str:
    """Before touching an existing data repo, show which repo and which GitHub account, and ask
    (UT-5). Returns 'use', 'new' or 'stop'."""
    who = login["login"] if login else None
    owner = repo.split("/")[0] if repo else None
    say(f"\nFound a star-triage data repo: {repo or '(no GitHub remote yet)'}")
    say(f"  Folder: {ROOT}")
    say(f"  GitHub CLI logged in as: {who or 'nobody (gh is not ready)'}")
    if who and owner and who.lower() != owner.lower():
        say(f"  Careful: this repo belongs to {owner}, but you're logged in as {who}.")
        say(f"  Settings and secrets need {owner}'s login. To switch: gh auth switch, or gh auth login.")
    if yes(f"Set up {repo or 'this repo'}" + (f" (logged in as {who})?" if who else "?"), default=False):
        return "use"
    if yes("Create a new data repo instead?", default=False):
        return "new"
    return "stop"


def migrate(target: Path) -> None:
    """Turn a v4 copy (with its own scripts/) into a data repo that uses the Action and plugin."""
    wf = target / ".github" / "workflows" / WORKFLOW_FILE
    old = (target / "scripts" / "select_new.py").exists() or \
        "scripts/select_new.py" in (wf.read_text(encoding="utf-8", errors="ignore") if wf.exists() else "")
    if not old:
        say("This repo already uses the new layout.")
        return
    say("This repo carries its own copy of the star-triage code (v4). From now on the code comes")
    say("from the star-triage Action (on GitHub) and plugin (on this computer), so updates arrive")
    say("by themselves. Your config.yml, notes, installs.json and approvals are kept.")
    say("Removed: " + ", ".join(p for p in OLD_ENGINE_PATHS if (target / p).exists()))
    say("Replaced: README.md and .github/workflows/star-triage.yml.  Added: star-triage.py")
    if not yes("Migrate now?"):
        return
    for rel in OLD_ENGINE_PATHS:
        if (target / rel).exists():
            git_in(target, "rm", "-r", "-q", "--ignore-unmatch", "--", rel)
            leftover = target / rel  # untracked files git rm left behind
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            elif leftover.exists():
                leftover.unlink()
    claude_dir = target / ".claude"
    if claude_dir.is_dir() and not any(p.is_file() for p in claude_dir.rglob("*")):
        shutil.rmtree(claude_dir, ignore_errors=True)
    copy_template(target, overwrite=("README.md", f".github/workflows/{WORKFLOW_FILE}", "star-triage.py"))
    ignore = target / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    for entry in (".triage/", ".star-triage/", "__pycache__/"):
        if entry not in lines:
            lines.append(entry)
    ignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import sync
    settings_hook = sync.hook_installed() and not running_as_plugin()
    if settings_hook and yes("Remove the old automatic-sync hook from settings.json (the plugin replaces it)?"):
        sync.install_hook(remove=True)
    commit_and_push(target, "Move to the star-triage Action and plugin")


def find_or_make_data_repo(login: dict | None) -> tuple[Path, str] | None:
    """(folder, stage) of the data repo to set up. Stage is '' for an existing repo the user
    confirmed, 'created' or 'approved' for one made just now."""
    if STAGE:
        return ROOT, STAGE
    if is_data_repo(ROOT):
        choice = confirm_existing(doctor.detect_repo(), login)
        if choice == "use":
            return ROOT, ""
        if choice == "stop":
            say("Nothing was changed.")
            return None
    else:
        cwd = Path.cwd()
        if cwd.resolve() == ENGINE_DIR:
            say("This folder is the star-triage engine, not a data repo, so setup will not use it.")
        elif (cwd / ".git").exists() and doctor.detect_repo():
            # Only an empty repo is offered: one with commits and files is somebody's project.
            empty = git_in(cwd, "rev-parse", "--verify", "-q", "HEAD").returncode != 0 \
                or not git_in(cwd, "ls-files").stdout.strip()
            if not empty:
                say(f"This folder ({cwd}) is a git repo that already has files, so setup will not use it")
                say("as your data repo. To use an existing empty repo, run setup from inside that one.")
            elif yes(f"Set up this folder ({cwd}) as your star-triage data repo?", default=False):
                start_on_main(cwd)
                written = copy_template(cwd)
                ensure_email(login, cwd)
                commit_and_push(cwd, "Start star-triage", written)
                return cwd, "created"
        say("You don't have a star-triage data repo yet. It's a small private GitHub repo that holds")
        say("your settings and notes; the workflow in it calls the star-triage Action.")
    made = create_data_repo(login)
    if not made:
        return None
    target, auth = made
    if auth:
        os.environ["STAR_TRIAGE_SETUP_AUTH"] = auth
    return target, "created" if STEP_BY_STEP else "approved"


def manual_steps(repo: str | None) -> None:
    r = repo or "<you>/<your-repo>"
    say("The GitHub CLI is not ready, so do these three by hand (or install it from")
    say("https://cli.github.com, run `gh auth login`, and run this script again):")
    say(f"  a) https://github.com/{r}/settings/actions  > Workflow permissions >")
    say("     tick 'Allow GitHub Actions to create and approve pull requests' > Save")
    say("  b) Run `claude setup-token`, copy the token, add it as CLAUDE_CODE_OAUTH_TOKEN at")
    say(f"     https://github.com/{r}/settings/secrets/actions/new  (don't share the screen it's shown on)")
    say(f"  c) https://github.com/{r}/actions > star-triage > Run workflow")


def existing_plan(repo: str, use_gh: bool, config_changed: bool, launcher_old: bool,
                  secret: set[str] | None, never_ran: bool, auth: str | None) -> list[str]:
    lines = []
    if launcher_old:
        lines.append("update star-triage.py in your data repo (the plugin has newer commands)")
    lines.append("commit and push config.yml" if config_changed
                 else "commit and push any starting file or commit GitHub doesn't have yet")
    if not use_gh:
        lines.append("show the GitHub steps to do by hand (the GitHub CLI is not ready)")
        return lines
    lines.append(f"check {repo}'s GitHub settings (Actions on, workflow may open pull requests) and fix "
                 "what is off")
    if secret:
        lines.append(f"keep the Claude credentials already stored ({', '.join(sorted(secret))})")
    else:
        lines.append("store your " + ("Anthropic API key" if auth == "apikey" else "Claude token")
                     + " as a repo secret (you paste it at a hidden prompt)")
    if never_ran:
        lines.append("wait until GitHub Actions has registered the workflow, then start the first run (3 repos)")
    return lines


def repair(repo: str) -> None:
    """--repair: make GitHub Actions register the workflow (UT-9)."""
    if workflow_registered(repo):
        say("GitHub Actions already has the star-triage workflow. Nothing to repair.")
        say(actions_hint(repo).split(" If ")[0])
        return
    say(f"GitHub Actions hasn't registered {WORKFLOW_PATH}. Setup will push a one-character change to")
    say("it (a trailing blank line), which makes GitHub look at the workflow again.")
    if not yes("Go ahead?"):
        return
    if nudge_workflow(ROOT) and wait_registered(repo):
        say("Fixed: GitHub Actions has the star-triage workflow. " + actions_hint(repo).split(" If ")[0])
    else:
        say("Still not registered. Check that the file is on the default branch on GitHub, and see "
            f"'Workflow not found' in {TROUBLESHOOTING_URL}")


# ----------------------------------------------------------------- main

def main() -> None:
    safe_console()
    args = set(sys.argv[1:])
    if "--check" in args:
        sys.exit(doctor.report(doctor.check_local()))
    if not sys.stdin.isatty():
        sys.exit("Setup asks questions, so it needs a terminal you can type in. Open a terminal window "
                 "and run it there yourself (to only check, add --check).")

    if not STAGE:
        say("star-triage setup. A few questions, then one plan to approve. Safe to run again.")
        if STEP_BY_STEP:
            say("Step by step: you are asked before each step.")

        step(1, "Checking your computer")
        if sys.version_info < (3, 9):
            sys.exit("Python 3.9 or newer is needed.")
        if importlib.util.find_spec("yaml") is None:
            if yes("PyYAML (a small library the scripts need) is missing. Install it now?"):
                subprocess.call([sys.executable, "-m", "pip", "install", "--user", "pyyaml"])
                importlib.invalidate_caches()
            if importlib.util.find_spec("yaml") is None:
                sys.exit("PyYAML is still missing. Install it with:\n  " + doctor.pyyaml_hint() + "\nthen run this again.")
    use_gh = gh_ready()
    login = gh_json("api", "user") if use_gh else None
    if not STAGE:
        say(f"GitHub CLI: {'logged in as ' + login['login'] if login else 'not available'}")

    found = find_or_make_data_repo(login)
    if not found:
        sys.exit(1)
    data, stage = found
    write_link(data=data, engine=ENGINE_DIR if running_as_plugin() else None)
    if data != ROOT or stage != STAGE:
        # Every module found the data repo at import time; start again pointed at this one.
        env = dict(os.environ, STAR_TRIAGE_DATA=str(data), STAR_TRIAGE_SETUP_STAGE=stage)
        sys.exit(subprocess.call([sys.executable, __file__, *sys.argv[1:]], cwd=data, env=env))
    approved = STAGE == "approved"
    if "--migrate" in args:
        say("\n== Upgrading from v4 " + "=" * 47)
        migrate(ROOT)
    repo = doctor.detect_repo()
    if not repo:
        sys.exit("Your data repo has no GitHub remote. Push it to GitHub first (git remote add origin ...).")
    say(f"\nData repo: {ROOT}  ({repo})")
    if "--repair" in args:
        if use_gh:
            repair(repo)
        else:
            say("--repair needs the GitHub CLI: install it and run gh auth login.")
        return

    # ---- questions (nothing changes yet)
    launcher, shipped = ROOT / "star-triage.py", TEMPLATE_DIR / "star-triage.py"
    launcher_old = launcher.exists() and \
        launcher.read_bytes().replace(b"\r\n", b"\n") != shipped.read_bytes().replace(b"\r\n", b"\n")
    original, cfg = read_config(CONFIG_PATH)
    text = original
    if not approved and "--skip-config" not in args:
        step(2, "Your settings (config.yml)")
        text = ask_config(original, cfg, login["login"] if login else None, repo.split("/")[0],
                          existing=not STAGE)
        if STEP_BY_STEP:
            save_config(CONFIG_PATH, original, text)
            original = text = CONFIG_PATH.read_text(encoding="utf-8")
    auth = os.environ.get("STAR_TRIAGE_SETUP_AUTH") or None
    secret = secret_names(repo) if use_gh else None
    never_ran = use_gh and gh_json("run", "list", "--repo", repo, "--workflow", WORKFLOW_FILE,
                                   "--limit", "1", "--json", "url") in ([], None)
    if use_gh and not secret and not auth and not STEP_BY_STEP:
        auth = ask_auth()
    if not approved and not STEP_BY_STEP:
        if not show_plan(existing_plan(repo, use_gh, text != original, launcher_old, secret, never_ran, auth)):
            say("Nothing was changed.")
            return

    # ---- apply
    step(3, "Saving and pushing your settings")
    if launcher_old and confirm("star-triage.py in your data repo is older than the plugin. Update it?"):
        launcher.write_bytes(shipped.read_bytes())
    if text != original:
        save_config(CONFIG_PATH, original, text)
    ensure_email(login)
    commit_settings()

    step(4, "GitHub settings")
    if use_gh:
        check_default_branch(repo, ROOT)
        enable_actions(repo)
        allow_pull_requests(repo)
        step(5, "Claude token (stored as a GitHub secret)")
        set_secret(repo, auth)
        step(6, "First run")
        if never_ran or STEP_BY_STEP:
            first_run(repo)
        else:
            say("The workflow has run before, so no first run is started. " + actions_hint(repo).split(" If ")[0])
    else:
        manual_steps(repo)

    step(7, "Automatic installs on this computer")
    automatic_installs()

    step(8, "Reading your notes in Obsidian")
    say(f"You need Obsidian 1.9 or newer (the table views use Bases). Open this folder as a vault: {ROOT}")
    say("Then open tracker/Star Triage.base. Notes arrive with `git pull`, after the first run has")
    say("finished and its pull request is merged. For new notes to appear on their own, install the")
    say("community plugin 'Git' (by Vinzent) and set 'Auto pull interval' to 10 minutes.")

    step(9, "Final check")
    doctor.report(doctor.check_local())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nStopped. Run it again any time; finished steps are skipped.")
