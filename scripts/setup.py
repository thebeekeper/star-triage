"""Guided setup for star-triage. Safe to run again; it asks before every change.

    python star-triage.py setup                from your data repo, once it exists
    python <engine>/scripts/setup.py           first time (the /star-triage:setup skill
                                               shows the exact path)
    ... --check          only check, change nothing (same as doctor)
    ... --skip-config    keep config.yml as it is
    ... --migrate        upgrade a v4 copy (with its own scripts/) to the Action + plugin

What it does:
  1. Finds your data repo, or creates one: a private GitHub repo with config.yml, tracker/,
     a launcher and a 20-line workflow that calls the star-triage Action.
  2. Writes config.yml from a few questions (about you, your projects).
  3. Sets your private GitHub commit email for that repo, if none is set, then commits and
     pushes your settings (and the starting files, if the first commit never happened).
  4. Lets the workflow open pull requests (a repo setting).
  5. Stores your Claude token as a repo secret. The token goes from `claude setup-token`
     straight into GitHub's hidden prompt. It is never written to a file or shown to Claude.
  6. Starts the first run.
Steps 1 (creating) and 4 to 6 use the GitHub CLI (https://cli.github.com). Without it you
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
from doctor import CONFIG_PATH, ROOT, WORKFLOW_FILE, find_claude, gh_json, gh_ready, run  # noqa: E402
from mode import set_mode_text  # noqa: E402
from paths import (DEFAULT_MAX_PER_RUN, ENGINE_DIR, MODES, TROUBLESHOOTING_URL, is_data_repo,  # noqa: E402
                   read_mode, running_as_plugin, safe_console, write_link)

TEMPLATE_DIR = ENGINE_DIR / "template"
# Files a v4 copy carried itself; the Action and the plugin provide them now.
OLD_ENGINE_PATHS = ["scripts", "prompts", "docs", "LICENSE", ".claude/skills/star-triage",
                    ".claude/skills/star-triage-sync", ".claude/skills/star-triage-setup"]

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
    import shutil
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        return subprocess.call([exe, *cmd[1:]], cwd=ROOT)
    except OSError as exc:
        say(f"Could not run {cmd[0]}: {exc}")
        return 127


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


def configure(gh_login: str | None, owner: str) -> bool:
    """Ask questions and rewrite config.yml. Returns True if it changed."""
    try:
        cfg = doctor.load_yaml(CONFIG_PATH) or {}
    except Exception:
        cfg = {}
    original = CONFIG_PATH.read_text(encoding="utf-8")
    text = original

    say("Press Enter to keep the value in [brackets].")
    say("Prefer to talk it through? Open Claude Code in this folder and run /star-triage:setup,")
    say("then come back and run: python star-triage.py setup --skip-config\n")

    current_user = cfg.get("github_user") or ""
    user = ask("Whose stars should be read (GitHub username)", current_user or gh_login or "")
    # Left empty, the workflow reads the stars of the repo's owner, which is usually you.
    if user != current_user and not (not current_user and user.lower() == owner.lower()):
        text = set_setting(text, "github_user", [f"github_user: {q(user)}"])

    say("\nA few lines about you: what you build, languages, what you want from repos.")
    say("Claude uses this to judge what is relevant to you and what is hype.")
    current_about = " ".join(str(cfg.get("about_me") or "").split())
    if current_about:
        say(f"Current: {current_about[:160]}{'...' if len(current_about) > 160 else ''}")
    about = ask_lines("Type it (empty line to finish, or just Enter to keep the current text):")
    if about:
        text = set_setting(text, "about_me", ["about_me: >"] + [f"  {line}" for line in about])

    say("\nYour projects and interests. Each becomes a 'relevant_to' label on notes.")
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

    say("\nWho approves installs? (Change it later with: python star-triage.py mode)")
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

    if text == original:
        say("\nconfig.yml unchanged.")
        return False
    CONFIG_PATH.write_text(text, encoding="utf-8")
    problems = [r for r in doctor.check_config() if r.status == "fail"]
    if problems:
        CONFIG_PATH.write_text(original, encoding="utf-8")
        say("\nThat produced an invalid config.yml, so nothing was changed:")
        for r in problems:
            say(f"  - {r.label}: {r.fix}")
        return False
    say("\nconfig.yml updated.")
    return True


# ----------------------------------------------------------------- git

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
        if yes(f"Use {noreply} for this repo?"):
            run(["git", "config", "user.email", noreply], cwd=where)
    elif not email:
        if yes(f"git has no commit email. Use your private GitHub address {noreply} for this repo?"):
            run(["git", "config", "user.email", noreply], cwd=where)
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


def commit_settings() -> None:
    """Commit and push config.yml, plus any starting file that never got committed (the first
    commit was declined or failed). Also pushes a commit that never reached GitHub."""
    rels = [src.relative_to(TEMPLATE_DIR).as_posix() for src in sorted(TEMPLATE_DIR.rglob("*"))
            if src.is_file() and "__pycache__" not in src.parts]
    rels = [rel for rel in rels if (ROOT / rel).exists()]
    listed = (git_in(ROOT, "-c", "core.quotePath=false", "ls-files", "--modified", "--others",
                     "--exclude-standard", "--", *rels).stdout
              + git_in(ROOT, "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--", *rels).stdout)
    changed = sorted({line for line in listed.splitlines() if line})
    first = git_in(ROOT, "rev-parse", "--verify", "-q", "HEAD").returncode != 0
    if changed:
        commit_and_push(ROOT, "Start star-triage" if first else "Configure star-triage", changed)
        return
    ahead = git_in(ROOT, "rev-list", "--count", "@{u}..HEAD")
    if not first and (ahead.returncode != 0 or ahead.stdout.strip() != "0"):
        if yes("Your last commit is not on GitHub yet. Push it now?"):
            push(ROOT)
        else:
            say("Skipped. The workflow reads everything from GitHub, so push before the first run.")
    else:
        say("Nothing to commit: GitHub already has your settings.")


# ----------------------------------------------------------------- GitHub settings

def allow_pull_requests(repo: str) -> None:
    perms = gh_json("api", f"repos/{repo}/actions/permissions/workflow")
    if perms is None:
        say(f"Could not read the Actions settings. Set it by hand: https://github.com/{repo}/settings/actions")
        return
    if perms.get("can_approve_pull_request_reviews"):
        say("Actions may already open pull requests.")
        return
    say("The workflow needs permission to open the review pull requests.")
    if not yes("Turn on 'Allow GitHub Actions to create and approve pull requests'?"):
        return
    res = run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions/workflow",
               "-f", f"default_workflow_permissions={perms.get('default_workflow_permissions', 'read')}",
               "-F", "can_approve_pull_request_reviews=true"])
    if res.returncode == 0:
        say("Done.")
    else:
        say(f"That did not work ({res.stderr.strip()}).")
        say(f"Tick it by hand under Workflow permissions: https://github.com/{repo}/settings/actions")


def enable_actions(repo: str) -> None:
    actions = gh_json("api", f"repos/{repo}/actions/permissions") or {}
    if actions and not actions.get("enabled") and yes("GitHub Actions is off for this repo. Turn it on?"):
        run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions", "-F", "enabled=true"])
    state = (gh_json("api", f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}") or {}).get("state", "")
    if state.startswith("disabled") and yes("The star-triage workflow is disabled. Enable it?"):
        run(["gh", "workflow", "enable", WORKFLOW_FILE, "--repo", repo])


def install_hint() -> str:
    if platform.system() == "Windows":
        return "irm https://claude.ai/install.ps1 | iex   (in PowerShell, then open a new window)"
    return "curl -fsSL https://claude.ai/install.sh | bash"


def set_secret(repo: str) -> None:
    names = {s.get("name") for s in (gh_json("secret", "list", "--repo", repo, "--json", "name") or [])}
    have = names & {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    if have:
        say(f"Already set: {', '.join(sorted(have))}.")
        if not yes("Replace it?", default=False):
            return
    say("How should the workflow use Claude?")
    say("  1) Your Claude subscription (Pro or Max). Recommended.")
    say("  2) An Anthropic API key (pay per use, from console.anthropic.com).")
    choice = ask("Choose 1 or 2", "1")
    if choice == "2":
        say("\nPaste your API key at the hidden prompt below. It goes straight to GitHub.")
        interactive(["gh", "secret", "set", "ANTHROPIC_API_KEY", "--repo", repo])
        return
    claude = find_claude()
    if not claude:
        say("Claude Code is not installed (or not on PATH). Install it, then run this script again:")
        say(f"  {install_hint()}")
        return
    say("\nNext, `claude setup-token` opens your browser to log in, then prints a long token.")
    say("Copy that token. You will paste it at a hidden prompt right after, which sends it")
    say("straight to GitHub as a secret. Do not paste it anywhere else.")
    if not yes("Ready to start?"):
        say("Skipped. Run setup again when you're ready.")
        return
    if interactive([claude, "setup-token"]) != 0:
        say("setup-token did not finish. Run this script again when ready.")
        return
    say("\nNow paste the token (input is hidden) and press Enter:")
    if interactive(["gh", "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN", "--repo", repo]) == 0:
        say("Secret saved. You can clear your terminal now so the token is not left on screen.")


def first_run(repo: str) -> None:
    try:
        per_run = int((doctor.load_yaml(CONFIG_PATH) or {}).get("max_per_run", DEFAULT_MAX_PER_RUN))
    except Exception:
        per_run = DEFAULT_MAX_PER_RUN
    say("Every repo you have starred so far gets reviewed, newest first. After this first run the")
    say(f"workflow takes {per_run} per run (max_per_run in config.yml), every 3 hours, until it has caught up.")
    say("Each review is a short Claude session. To go slower, lower max_per_run; to leave repos out,")
    say("list them under ignore in config.yml.")
    if not yes("Start the first run now, with 3 repos?"):
        say(f"Later: GitHub > Actions > star-triage > Run workflow, or gh workflow run {WORKFLOW_FILE} --repo {repo}")
        return
    res = run(["gh", "workflow", "run", WORKFLOW_FILE, "--repo", repo, "-f", "max=3"])
    if res.returncode != 0:
        say("Could not start it: " + res.stderr.strip())
        return
    time.sleep(4)
    runs = gh_json("run", "list", "--repo", repo, "--workflow", WORKFLOW_FILE, "--limit", "1", "--json", "url")
    url = runs[0]["url"] if runs else f"https://github.com/{repo}/actions"
    say(f"Started. Watch it here (takes about 2 to 5 minutes): {url}")
    say("When it has finished:")
    say("  1. Check the run is green. If it is red, the run page says why and how to fix it.")
    say(f"  2. Merge the pull request if one is waiting: https://github.com/{repo}/pulls")
    say("     (a run that approves no installs merges its notes by itself).")
    say(f"  3. Get the notes onto this computer: run `git pull` in {ROOT}")


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


def git_in(path: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=path, timeout=timeout)


def commit_and_push(path: Path, message: str, files: list[str] | None = None) -> None:
    """Commit and push. With `files`, only those paths are staged and committed, and they are
    listed first; anything else in the folder is left alone."""
    only = ["--", *files] if files else []
    if files is None:
        git_in(path, "add", "-A")
    elif files:
        git_in(path, "add", *only)
    else:
        return
    if not git_in(path, "status", "--porcelain", *only).stdout.strip():
        return
    if files:
        say("Files to commit:")
        for rel in files:
            say(f"  {rel}")
    if not yes(f"Commit these changes ('{message}') and push them to GitHub?"):
        say("Skipped. Commit and push them yourself before the next run.")
        return
    res = git_in(path, "commit", "-q", "-m", message, *only)
    if res.returncode:
        say(res.stderr.strip() or res.stdout.strip())
        return
    push(path)


def create_data_repo(login: dict | None) -> Path | None:
    """Make a new private GitHub repo with the template, cloned next to the user's documents."""
    if not login:
        say("To create your data repo automatically, install the GitHub CLI (https://cli.github.com),")
        say("run `gh auth login`, then run setup again. Or by hand: create an empty private repo on")
        say("GitHub, clone it, and run setup from inside that folder.")
        return None
    name = ask("Name for your new private GitHub repo", "my-stars")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name):
        say("That is not a valid repo name.")
        return None
    parent = Path(ask("Folder to put it in", str(Path.home() / "Documents"))).expanduser()
    target = parent / name
    if target.exists():
        say(f"{target} already exists. Pick another name, or run setup from inside that folder.")
        return None
    parent.mkdir(parents=True, exist_ok=True)
    if not yes(f"Create github.com/{login['login']}/{name} (private) and clone it to {target}?"):
        return None
    res = run(["gh", "repo", "create", name, "--private", "--clone",
               "--description", "My GitHub stars, reviewed by star-triage"], cwd=parent, timeout=180)
    if res.returncode or not target.exists():
        say("Could not create it: " + (res.stderr.strip() or res.stdout.strip()))
        return None
    written = copy_template(target)
    ensure_email(login, target)
    commit_and_push(target, "Start star-triage", written)
    return target


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


def find_or_make_data_repo(login: dict | None) -> Path | None:
    if is_data_repo(ROOT):
        return ROOT
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
            written = copy_template(cwd)
            ensure_email(login, cwd)
            commit_and_push(cwd, "Start star-triage", written)
            return cwd
    say("You don't have a star-triage data repo yet. It's a small private GitHub repo that holds")
    say("your settings and notes; the workflow in it calls the star-triage Action.")
    return create_data_repo(login)


def manual_steps(repo: str | None) -> None:
    r = repo or "<you>/<your-repo>"
    say("The GitHub CLI is not ready, so do these three by hand (or install it from")
    say("https://cli.github.com, run `gh auth login`, and run this script again):")
    say(f"  a) https://github.com/{r}/settings/actions  > Workflow permissions >")
    say("     tick 'Allow GitHub Actions to create and approve pull requests' > Save")
    say("  b) Run `claude setup-token`, copy the token, add it as CLAUDE_CODE_OAUTH_TOKEN at")
    say(f"     https://github.com/{r}/settings/secrets/actions/new")
    say(f"  c) https://github.com/{r}/actions > star-triage > Run workflow")


# ----------------------------------------------------------------- main

def main() -> None:
    safe_console()
    args = set(sys.argv[1:])
    if "--check" in args:
        sys.exit(doctor.report(doctor.check_local()))
    if not sys.stdin.isatty():
        sys.exit("Setup asks questions, so it needs a terminal you can type in. Open a terminal window "
                 "and run it there yourself (to only check, add --check).")

    say("star-triage setup. It asks before changing anything, and is safe to run again.")

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
    say(f"GitHub CLI: {'logged in as ' + login['login'] if login else 'not available'}")

    data = find_or_make_data_repo(login)
    if not data:
        sys.exit(1)
    write_link(data=data, engine=ENGINE_DIR if running_as_plugin() else None)
    if data != ROOT:
        # Every module found the data repo at import time; start again pointed at the new one.
        env = dict(os.environ, STAR_TRIAGE_DATA=str(data))
        sys.exit(subprocess.call([sys.executable, __file__, *sys.argv[1:]], cwd=data, env=env))
    if "--migrate" in args:
        say("\n== Upgrading from v4 " + "=" * 47)
        migrate(ROOT)
    repo = doctor.detect_repo()
    if not repo:
        sys.exit("Your data repo has no GitHub remote. Push it to GitHub first (git remote add origin ...).")
    say(f"Data repo: {ROOT}  ({repo})")

    launcher, shipped = ROOT / "star-triage.py", TEMPLATE_DIR / "star-triage.py"
    if launcher.exists() and launcher.read_bytes().replace(b"\r\n", b"\n") != shipped.read_bytes().replace(b"\r\n", b"\n"):
        if yes("star-triage.py in your data repo is older than the plugin (it lacks the newer commands). Update it?"):
            launcher.write_bytes(shipped.read_bytes())

    step(2, "Your settings (config.yml)")
    if "--skip-config" in args:
        say("Skipped (--skip-config).")
    else:
        configure(login["login"] if login else None, repo.split("/")[0])

    step(3, "Commit email and pushing your settings")
    ensure_email(login)
    commit_settings()

    step(4, "GitHub settings")
    if use_gh:
        enable_actions(repo)
        allow_pull_requests(repo)
        step(5, "Claude token (stored as a GitHub secret)")
        set_secret(repo)
        step(6, "First run")
        first_run(repo)
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
