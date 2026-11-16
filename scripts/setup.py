"""Guided setup for star-triage. Safe to run again.

    python "$HOME/.claude/star-triage.py" setup   first time, from any folder (the plugin keeps
                                                  this launcher there; the path never changes)
    python star-triage.py setup                   from your data repo, once it exists
    ... --step-by-step   ask before every single step, as setup did before 5.4
    ... --check          only check, change nothing (same as doctor)
    ... --skip-config    keep config.yml as it is
    ... --repair         push a small change to the workflow file, so GitHub Actions registers it
    ... --migrate        upgrade a v4 copy (with its own scripts/) to the Action + plugin
    python "$HOME/.claude/star-triage.py" token   store the Claude token (--api-key: an API key)

Without questions (the /star-triage:setup skill runs it like this, from Claude Code):
    ... --new-repo NAME [--folder PARENT]   create a new data repo (default folder: ~/Documents)
    ... --repo OWNER/NAME [--folder PARENT] use an existing one (cloned first if it isn't here)
    ... --mode manual|auto|auto-plus  --max N  --obsidian | --no-obsidian  --no-auto-update
    ... --check-every 1|3|6|12|24   how often GitHub checks for new stars (the workflow's cron)
    ... --install-wait 0|1|6|24     auto modes: hours before a reviewed tool installs (0 = no wait)
    Without --yes it only prints the plan and changes nothing; with --yes it carries it out.
    The token is never part of this: setup then prints the one `token` command to run in your
    own terminal, because the token must never pass through Claude.

How it works: a few questions first (which repo, your projects, the star-triage mode, how often
to check for new stars, how long installs wait, how the workflow uses Claude), then ONE plan and one "Go ahead?". Then it runs straight through:
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
import schedule as sched  # noqa: E402
from doctor import (CONFIG_PATH, ROOT, WORKFLOW_FILE, actions_hint, find_claude, gh_json,  # noqa: E402
                    gh_ready, run, workflow_registered)
from mode import set_mode_text  # noqa: E402
from paths import (AUTO_PLUS_SENTENCE, DEFAULT_MAX_PER_RUN, ENGINE_DIR, MODES, TROUBLESHOOTING_URL,  # noqa: E402
                   config_on, is_data_repo, launcher_command, linked_data_root, merge_policy, merge_rule,
                   parse_simple_config, read_mode, read_simple_config, running_as_plugin, safe_console,
                   wait_hours, write_link)

TEMPLATE_DIR = ENGINE_DIR / "template"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
# Files a v4 copy carried itself; the Action and the plugin provide them now.
OLD_ENGINE_PATHS = ["scripts", "prompts", "docs", "LICENSE", ".claude/skills/star-triage",
                    ".claude/skills/star-triage-sync", ".claude/skills/star-triage-setup"]
# Set by setup for the copy of itself it starts inside a data repo it has just made.
STAGE = os.environ.get("STAR_TRIAGE_SETUP_STAGE", "")  # "created" or "approved" (plan said yes)
STEP_BY_STEP = "--step-by-step" in sys.argv[1:]


def flag_value(name: str, argv: list[str] | None = None) -> str | None:
    """The value of `--name VALUE` or `--name=VALUE`, or None."""
    argv = sys.argv[1:] if argv is None else argv
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


# Answers given as flags (the /star-triage:setup skill): setup then never asks anything.
VALUE_FLAGS = ("--mode", "--max", "--repo", "--new-repo", "--folder", "--check-every", "--install-wait")
SWITCH_FLAGS = ("--yes", "--obsidian", "--no-obsidian", "--no-auto-update")
NONINTERACTIVE = any(flag_value(f) is not None for f in VALUE_FLAGS) or bool(set(SWITCH_FLAGS) & set(sys.argv[1:]))
GO = "--yes" in sys.argv[1:]  # the plan was shown and approved (in the chat)

TOP_KEY = re.compile(r"^[A-Za-z_][\w-]*:")


# ----------------------------------------------------------------- prompts

def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, title: str) -> None:
    say(f"\n== {n}. {title} " + "=" * max(3, 60 - len(title)))


def ask(question: str, default: str = "") -> str:
    if NONINTERACTIVE:
        return default
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{hint}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def yes(question: str, default: bool = True) -> bool:
    if NONINTERACTIVE:
        return False  # without a terminal nobody can answer: never treat that as a yes
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
    if NONINTERACTIVE:
        return []
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


def ask_config(text: str, cfg: dict, gh_login: str | None, owner: str, existing: bool,
               current_check: int | None = None) -> str:
    """Ask the config questions and return config.yml's new text (nothing is written here).
    current_check: how often the repo's workflow checks now (its cron), if known."""
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
    names = list(SETUP_MODE_ORDER)
    for n, name in enumerate(names, 1):
        say(f"  {n}) {name + ':':11}{MODES[name]}" + ("  (recommended)" if name == "auto-plus" else ""))
        if name == "auto-plus":
            say(f"     {AUTO_PLUS_SENTENCE}")
    say("High-risk and blocked items are never installed automatically, in any mode.")
    current = read_mode(cfg)
    # Enter keeps the current mode (manual in a new config): an auto mode is always a real choice.
    pick = ask("Choose 1, 2 or 3", str(names.index(current) + 1))
    chosen = names[int(pick) - 1] if pick in ("1", "2", "3") else current
    # Written only when it is a real choice: keeping the current mode never rewrites an old
    # config that still uses auto_merge.
    if chosen != current or "mode" in cfg:
        text = set_mode_text(text, chosen)

    now = current_check or sched.number_in(text, "check_every_hours") or sched.RECOMMENDED_CHECK
    hours = ask_choice(sched.CHECK_QUESTION + " (Change it later with: python star-triage.py schedule)",
                       sched.CHECK_CHOICES, now, sched.RECOMMENDED_CHECK)
    if hours != now:
        text = sched.set_check_text(text, hours)
    if chosen != "manual":
        now = wait_hours(cfg)
        wait = ask_choice(sched.WAIT_QUESTION + " (Time to untick it or pause first.)",
                          sched.WAIT_CHOICES, now, sched.RECOMMENDED_WAIT)
        if wait != now:
            text = sched.set_wait_text(text, wait)

    uses = config_on(cfg, "obsidian")
    obsidian = yes("\nDo you read your notes in Obsidian? (No: use `python star-triage.py list` instead)",
                   default=uses)
    if obsidian != uses or "obsidian" in cfg:
        text = set_flag_setting(text, "obsidian", obsidian)
    return text


def ask_choice(question: str, choices: dict[int, str], current: int, recommended: int) -> int:
    """A numbered menu; Enter keeps `current`."""
    say(f"\n{question}")
    keys = list(choices)
    for n, value in enumerate(keys, 1):
        tags = [t for t, on in (("recommended", value == recommended), ("current", value == current)) if on]
        say(f"  {n}) {choices[value]}" + (f"  ({', '.join(tags)})" if tags else ""))
    default = str(keys.index(current) + 1) if current in keys else ""
    pick = ask(f"Choose 1 to {len(keys)}", default)
    return keys[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(keys) else current


# Setup shows the recommended mode first (mode.py keeps the MODES order).
SETUP_MODE_ORDER = ("auto-plus", "auto", "manual")
FLAG_COMMENTS = {
    "obsidian": ["# Do you read the notes in Obsidian? With false, setup skips the Obsidian tips. Without",
                 "# Obsidian: python star-triage.py list (or /star-triage:list in Claude Code)."],
    "auto_update": ["# Keep the star-triage plugin up to date by itself: when your reviews come from a newer",
                    "# star-triage, it updates itself in the background (at most once a day). false turns it off."],
}


def set_flag_setting(text: str, key: str, value: bool) -> str:
    """Set a true/false setting. A missing one is added with its comment, above `contexts`."""
    line = f"{key}: {'true' if value else 'false'}"
    if block_span(text.splitlines(), key):
        return set_setting(text, key, [line])
    return set_setting(text, key, FLAG_COMMENTS.get(key, []) + [line], before="contexts")


def flag_problems(argv: list[str] | None = None) -> list[str]:
    """What is wrong with the answer flags, in plain words (empty when they are fine)."""
    argv = sys.argv[1:] if argv is None else argv
    out = []
    mode, mpr = flag_value("--mode", argv), flag_value("--max", argv)
    new, repo = flag_value("--new-repo", argv), flag_value("--repo", argv)
    if mode is not None and mode not in MODES:
        out.append(f"--mode must be one of: {', '.join(MODES)}")
    if mpr is not None and not (mpr.isdigit() and 1 <= int(mpr) <= 25):
        out.append("--max must be a whole number from 1 to 25")
    if new is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", new):
        out.append(f"'{new}' is not a valid repo name (letters, numbers, . _ and - only)")
    if repo is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", repo):
        out.append(f"--repo must look like owner/name, not '{repo}'")
    if new and repo:
        out.append("give --repo (an existing data repo) or --new-repo (a new one), not both")
    if {"--obsidian", "--no-obsidian"} <= set(argv):
        out.append("give --obsidian or --no-obsidian, not both")
    check, wait = flag_value("--check-every", argv), flag_value("--install-wait", argv)
    if check is not None and not (check.isdigit() and int(check) in sched.CHECK_CHOICES):
        out.append("--check-every must be 1, 3, 6, 12 or 24 (hours)")
    if wait is not None and not sched.wait_ok(wait):
        out.append("--install-wait must be a whole number of hours from 0 to 168 (0 = no wait)")
    if "--step-by-step" in argv:
        out.append("--step-by-step asks questions, so it can't be used with answer flags")
    return out


def config_from_flags(text: str, argv: list[str] | None = None) -> str:
    """config.yml's new text from the answer flags (nothing is written here)."""
    argv = sys.argv[1:] if argv is None else argv
    mode, mpr = flag_value("--mode", argv), flag_value("--max", argv)
    if mode in MODES:
        text = set_mode_text(text, mode)
    if mpr and mpr.isdigit():
        text = set_setting(text, "max_per_run", [f"max_per_run: {int(mpr)}"])
    if "--obsidian" in argv or "--no-obsidian" in argv:
        text = set_flag_setting(text, "obsidian", "--obsidian" in argv)
    if "--no-auto-update" in argv:
        text = set_flag_setting(text, "auto_update", False)
    check, wait = flag_value("--check-every", argv), flag_value("--install-wait", argv)
    if check and check.isdigit() and int(check) in sched.CHECK_CHOICES:
        text = sched.set_check_text(text, int(check))
    if wait is not None and sched.wait_ok(wait):
        text = sched.set_wait_text(text, int(wait))
    return text


def check_change(original: str, text: str) -> int | None:
    """The new check interval when this run of setup changes it, else None."""
    want = sched.number_in(text, "check_every_hours")
    return want if want in sched.CHECK_CHOICES and want != sched.number_in(original, "check_every_hours") else None


def apply_check(path: Path, original: str, text: str) -> None:
    """Write a changed check interval into the workflow's cron line (committed with config.yml)."""
    hours = check_change(original, text)
    if hours and sched.write_cron(path, hours):
        say(f"Workflow: GitHub now checks for new stars {sched.check_words(hours)} "
            f"(cron \"{sched.cron_for(hours)}\", UTC).")


def per_run_of(text: str) -> int:
    n = sched.number_in(text, "max_per_run")
    return n if n and 1 <= n <= 25 else DEFAULT_MAX_PER_RUN


def first_run_words(text: str) -> str:
    """Why the first run is small (UT-21)."""
    return (f"start the first run (3 repos, so you see results in a few minutes; after that "
            f"{per_run_of(text)} per run)")


def timers_line(text: str, check_hours: int | None = None) -> str:
    """Both timers in one line for the plan (UT-19)."""
    cfg = parse_simple_config(text)
    hours = check_hours or sched.number_in(text, "check_every_hours")
    mode, wait = read_mode(cfg), wait_hours(cfg)
    installs = ("you approve every install (manual mode)" if mode == "manual" else
                f"in {mode} mode a reviewed tool installs by itself "
                + ("right after its review" if wait == 0 else f"{wait} h after its review"))
    return (f"timers: GitHub checks for new stars {sched.check_words(hours)}; {installs} "
            "(change either later: python star-triage.py schedule)")


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


def set_secret(repo: str, how: str | None = None, ask_replace: bool | None = None) -> bool:
    """Store the Claude token (or API key) as a repo secret, through hidden prompts in this
    terminal. True when a credential is stored (now or before)."""
    ask_replace = STEP_BY_STEP if ask_replace is None else ask_replace
    have = secret_names(repo) or set()
    if have:
        say(f"Claude credentials already set: {', '.join(sorted(have))}.")
        if not ask_replace or not yes("Replace it?", default=False):
            if not ask_replace:
                say(f"  (To replace it, in your own terminal: {launcher_command('token')})")
            return True
    how = how or ask_auth()
    where = f"your repo > Settings > Secrets and variables > Actions  (https://github.com/{repo}/settings/secrets/actions)"
    if how == "apikey":
        explain("Store your Anthropic API key as a repo secret (ANTHROPIC_API_KEY)",
                "the workflow uses it to run Claude for the reviews.", where)
        say("\nPaste your API key at the hidden prompt below. It goes straight to GitHub.")
        if interactive(["gh", "secret", "set", "ANTHROPIC_API_KEY", "--repo", repo]) == 0:
            say("Secret saved.")
            return True
        say(f"That did not work. Add ANTHROPIC_API_KEY by hand at {where}")
        return False
    claude = find_claude()
    if not claude:
        say("Claude Code is not installed (or not on PATH). Install it, then run this script again:")
        say(f"  {install_hint()}")
        return False
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
        return False
    if not STEP_BY_STEP:
        ask("Press Enter to start")
    if interactive([claude, "setup-token"]) != 0:
        say("setup-token did not finish. Run setup again when ready.")
        return False
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
    return saved


def real_terminal() -> bool:
    """Both ends are a terminal a person types in and reads. stdin alone is not enough: on
    Windows the NUL device counts as a tty, and inside Claude Code stdout is always a pipe."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def token_main() -> int:
    """`star-triage.py token`: the one setup step that stays in the user's own terminal,
    because `claude setup-token` prints the token and Claude must never see it."""
    if not real_terminal():
        say("This step shows your Claude token on screen, so it only runs in your own terminal")
        say("(PowerShell on Windows, Terminal on Mac), never inside Claude Code. Run there:")
        say(f"  {launcher_command('token')}")
        return 1
    if not gh_ready():
        say("The GitHub CLI is not logged in. Run `gh auth login`, then this again.")
        return 1
    repo = doctor.detect_repo() if is_data_repo(ROOT) else None
    if not repo:
        say("No star-triage data repo with a GitHub remote was found. Run /star-triage:setup in Claude")
        say("Code first (or: " + launcher_command("setup") + ").")
        return 1
    who = (gh_json("api", "user") or {}).get("login") or "unknown"
    owner = repo.split("/")[0]
    say(f"Stores the Claude credential for {repo} as a GitHub secret.")
    say(f"  GitHub account: {who} (the GitHub CLI's login, `gh auth status`)")
    say(f"  Secret goes to: {repo}  (Settings > Secrets and variables > Actions)")
    if who.lower() != owner.lower():
        say(f"  Careful: {repo} belongs to {owner}, not {who}.")
    if not yes("Right account?"):
        say("Nothing was changed. Switch with `gh auth switch` (or `gh auth login`), then run this again.")
        return 1
    ok = set_secret(repo, "apikey" if "--api-key" in sys.argv[1:] else "oauth", ask_replace=True)
    if ok:
        say("\nDone. Go back to Claude Code and say so; setup carries on from there.")
    return 0 if ok else 1


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
        text = (path / "config.yml").read_text(encoding="utf-8")
    except OSError:
        text = ""
    cfg, every = parse_simple_config(text), sched.check_words(sched.workflow_hours(path)[0])
    say("Every repo you have starred so far gets reviewed, newest first. This first run takes 3, so you")
    say(f"see results in a few minutes; after that {per_run_of(text)} per run (max_per_run in config.yml),")
    say(f"{every}, until it has caught up. Each review is a short Claude session. To go slower, lower")
    say("max_per_run; to leave repos out, list them under ignore in config.yml. With no stars yet, star a")
    say("few repos first.")
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
    say(f"  2. The pull request with the notes: https://github.com/{repo}/pulls")
    rule = merge_rule(cfg)
    say("     " + (rule if merge_policy(cfg) == "never" else f"In {read_mode(cfg)} mode: {rule[0].lower()}{rule[1:]}"))
    say(f"  3. Get the notes onto this computer: run `git pull` in {path}")


def check_default_branch(repo: str, path: Path) -> None:
    """Say so if the branch setup pushed is not the repo's default (nothing assumes a name)."""
    info = gh_json("repo", "view", repo, "--json", "defaultBranchRef") or {}
    default = (info.get("defaultBranchRef") or {}).get("name")
    mine = git_in(path, "branch", "--show-current").stdout.strip()
    if default and mine and default != mine:
        say(f"Note: GitHub's default branch for {repo} is '{default}', but this folder is on '{mine}'.")
        say("The workflow runs on the default branch. Switch with: git switch " + default)


def timers() -> None:
    """Both timers, with their numbers and where each is set (UT-19)."""
    cfg = read_simple_config(CONFIG_PATH)
    for line in sched.summary(ROOT, cfg):
        say(line)
    problem = sched.mismatch(ROOT, cfg)
    if problem:
        say(f"Note: {problem}.")
    say("Change either: python star-triage.py schedule (or /star-triage:schedule in Claude Code).\n")


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


def plugin_updates() -> None:
    """Keep the star-triage plugin itself up to date (auto_update in config.yml, default on)."""
    import sync
    on = config_on(read_simple_config(CONFIG_PATH), "auto_update")
    state = sync.marketplace_auto_update(on)
    if not on:
        say("Plugin updates: off (auto_update: false in config.yml)."
            + (" Claude Code's auto-update for the star-triage marketplace is off again too." if state == "changed" else ""))
        return
    if state in ("changed", "already"):
        say("Plugin updates: on. Claude Code updates the star-triage plugin by itself: auto-update is on for")
        say("its marketplace (`autoUpdate` in ~/.claude/settings.json"
            + (", backup: settings.json.bak)." if state == "changed" else ")."))
    else:
        say("Plugin updates: on. When your reviews come from a newer star-triage, the plugin updates itself")
        say("when Claude Code starts (at most once a day). Claude Code can do it too: /plugin > Marketplaces >")
        say("star-triage > Enable auto-update.")


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


TOKEN_BY_COMMAND = ("then you store your Claude token as a repo secret with one short command in your own "
                    "terminal (Claude never sees the token)")


def new_repo_plan(login: str, name: str, target: Path, auth: str | None, text: str = "") -> list[str]:
    """auth: 'apikey', 'oauth', or 'command' (the token step is left to `star-triage.py token`).
    text: config.yml's new text, for the timers line."""
    token = ("store your Anthropic API key as a repo secret (you paste it at a hidden prompt)" if auth == "apikey"
             else TOKEN_BY_COMMAND if auth == "command"
             else "store your Claude token as a repo secret (`claude setup-token`, then you paste it at a "
                  "hidden prompt)")
    return [f"create the private repo {login}/{name} in {target}, on branch main, with your settings, "
            "and push it",
            "make sure GitHub Actions is on, and let its workflow open pull requests (repo settings)",
            token,
            ("once the token is stored: " if auth == "command" else "")
            + "wait until GitHub Actions has registered the workflow, then " + first_run_words(text)] \
        + ([timers_line(text)] if text else [])


def plan_only(lines: list[str]) -> None:
    """Without --yes the flag path stops here: the plan, and nothing changed."""
    say("\nHere is what setup will do:")
    for line in lines:
        say(f"  - {line}")
    say("\nNothing was changed. To do it, run the same command again with --yes.")
    sys.exit(0)


def make_repo(login: dict, name: str, parent: Path, original: str, text: str) -> Path | None:
    """Create the private GitHub repo, fill it from the template with config.yml's new text,
    commit and push. The plan has been approved before this is called."""
    target = parent / name
    parent.mkdir(parents=True, exist_ok=True)
    res = run(["gh", "repo", "create", name, "--private", "--clone",
               "--description", "My GitHub stars, reviewed by star-triage"], cwd=parent, timeout=180)
    if res.returncode or not target.exists():
        say("Could not create it: " + (res.stderr.strip() or res.stdout.strip()))
        return None
    start_on_main(target)
    written = copy_template(target)
    if text != original and save_config(target / "config.yml", original, text):
        apply_check(target, original, text)
    ensure_email(login, target)
    commit_and_push(target, "Start star-triage", written)
    return target


def flags_target(login: dict) -> tuple[Path, str] | None:
    """The flag path's (folder, stage): an existing data repo, one cloned or created now (after
    --yes), or None after saying what is missing. Without --yes, plans that change something
    on GitHub or on disk are printed and setup stops."""
    new, want = flag_value("--new-repo"), flag_value("--repo")
    parent = Path(flag_value("--folder") or Path.home() / "Documents").expanduser()
    account = f"GitHub account: {login['login']} (to use another: gh auth switch, then run setup again)"
    if new:
        target = parent / new
        if target.exists():
            say(f"{target} already exists. Choose another name, or another folder with --folder.")
            return None
        original = (TEMPLATE_DIR / "config.yml").read_text(encoding="utf-8")
        text = config_from_flags(original)
        if not GO:
            plan_only([account] + new_repo_plan(login["login"], new, target, "command", text))
        made = make_repo(login, new, parent, original, text)
        return (made, "approved") if made else None

    here = [p for p in (ROOT if is_data_repo(ROOT) else None, linked_data_root()) if p and is_data_repo(p)]
    if not want:
        if here:
            return here[0], ""
        say("No star-triage data repo on this computer. Say which one to use: --repo OWNER/NAME for one")
        say("you already have on GitHub, or --new-repo NAME to create a new private one.")
        return None
    for folder in here:
        if (doctor.detect_repo(folder) or "").lower() == want.lower():
            return folder, ""
    target = parent / want.split("/", 1)[1]
    if target.exists():
        say(f"{target} already exists and is not a clone of {want}. Choose another folder with --folder.")
        return None
    if not GO:
        plan_only([account,
                   f"clone {want} into {target} and use it as your star-triage data repo on this computer",
                   "apply your answers to config.yml, then commit and push it (if anything changed)",
                   "check the repo's GitHub settings (Actions on, workflow may open pull requests) and fix what is off",
                   "if no Claude token is stored yet: " + TOKEN_BY_COMMAND,
                   first_run_words(config_from_flags((TEMPLATE_DIR / "config.yml").read_text(encoding="utf-8")))
                   + " if the workflow has never run"])
    parent.mkdir(parents=True, exist_ok=True)
    res = run(["gh", "repo", "clone", want, str(target)], cwd=parent, timeout=300)
    if res.returncode or not is_data_repo(target):
        say(f"Could not use {want}: " + (res.stderr.strip() or res.stdout.strip()
                                         or "it has no config.yml and tracker/, so it is not a star-triage data repo."))
        return None
    return target, "approved"


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
        if not show_plan(new_repo_plan(login["login"], name, target, auth, text)):
            say("Nothing was changed.")
            return None

    made = make_repo(login, name, parent, original, text)
    return (made, auth) if made else None


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
    if NONINTERACTIVE:
        return flags_target(login)
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
                  secret: set[str] | None, never_ran: bool, auth: str | None,
                  text: str = "", check: int | None = None) -> list[str]:
    lines = []
    if launcher_old:
        lines.append("update star-triage.py in your data repo (the plugin has newer commands)")
    if check:
        lines.append(f"set the workflow's schedule to check for new stars {sched.check_words(check)} "
                     "(its cron line), together with config.yml")
    lines.append("commit and push config.yml" if config_changed
                 else "commit and push any starting file or commit GitHub doesn't have yet")
    if not use_gh:
        lines.append("show the GitHub steps to do by hand (the GitHub CLI is not ready)")
        return lines
    lines.append(f"check {repo}'s GitHub settings (Actions on, workflow may open pull requests) and fix "
                 "what is off")
    if secret:
        lines.append(f"keep the Claude credentials already stored ({', '.join(sorted(secret))})")
    elif auth == "command":
        lines.append(TOKEN_BY_COMMAND)
    else:
        lines.append("store your " + ("Anthropic API key" if auth == "apikey" else "Claude token")
                     + " as a repo secret (you paste it at a hidden prompt)")
    if never_ran:
        lines.append(("once the token is stored: " if auth == "command" and not secret else "")
                     + "wait until GitHub Actions has registered the workflow, then " + first_run_words(text))
    if text:
        lines.append(timers_line(text, check or sched.workflow_hours(ROOT)[0]))
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
    if "--token" in args:
        sys.exit(token_main())
    if "--check" in args:
        sys.exit(doctor.report(doctor.check_local()))
    if NONINTERACTIVE:
        problems = flag_problems()
        if problems:
            for problem in problems:
                say(problem)
            sys.exit(2)
    elif not real_terminal():
        sys.exit("Setup asks questions, so it needs a terminal you can type in. In Claude Code, run "
                 "/star-triage:setup instead. Or open a terminal window and run it there yourself (to only "
                 "check, add --check).")

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
    if NONINTERACTIVE and not login:
        if not doctor.gh_installed():
            sys.exit("The GitHub CLI is not installed. Install it (Windows: winget install --id GitHub.cli, "
                     "Mac: brew install gh, others: https://cli.github.com), then run `gh auth login` in a terminal.")
        sys.exit("The GitHub CLI is not logged in. Run `gh auth login` in a terminal, then setup again.")

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
    if NONINTERACTIVE:
        text = config_from_flags(original)
    elif not approved and "--skip-config" not in args:
        step(2, "Your settings (config.yml)")
        text = ask_config(original, cfg, login["login"] if login else None, repo.split("/")[0],
                          existing=not STAGE, current_check=sched.workflow_hours(ROOT)[0])
        if STEP_BY_STEP:
            if save_config(CONFIG_PATH, original, text):
                apply_check(ROOT, original, text)
            original = text = CONFIG_PATH.read_text(encoding="utf-8")
    auth = "command" if NONINTERACTIVE else os.environ.get("STAR_TRIAGE_SETUP_AUTH") or None
    secret = secret_names(repo) if use_gh else None
    never_ran = use_gh and gh_json("run", "list", "--repo", repo, "--workflow", WORKFLOW_FILE,
                                   "--limit", "1", "--json", "url") in ([], None)
    if use_gh and not secret and not auth and not STEP_BY_STEP:
        auth = ask_auth()
    if not approved and not STEP_BY_STEP:
        lines = existing_plan(repo, use_gh, text != original, launcher_old, secret, never_ran, auth,
                              text, check_change(original, text))
        if NONINTERACTIVE:
            owner, who = repo.split("/")[0], login["login"]
            account = f"use {repo} in {ROOT}, as GitHub account {who}"
            if owner.lower() != who.lower():
                account += f" (careful: the repo belongs to {owner}; settings and secrets need {owner}'s login)"
            if not GO:
                plan_only([account] + lines)
        elif not show_plan(lines):
            say("Nothing was changed.")
            return

    # ---- apply
    step(3, "Saving and pushing your settings")
    if launcher_old and confirm("star-triage.py in your data repo is older than the plugin. Update it?"):
        launcher.write_bytes(shipped.read_bytes())
    if text != original and save_config(CONFIG_PATH, original, text):
        apply_check(ROOT, original, text)
    ensure_email(login)
    commit_settings()

    step(4, "GitHub settings")
    if use_gh:
        check_default_branch(repo, ROOT)
        enable_actions(repo)
        allow_pull_requests(repo)
        step(5, "Claude token (stored as a GitHub secret)")
        if NONINTERACTIVE:
            stored = bool(secret)
            if stored:
                say(f"Claude credentials already set: {', '.join(sorted(secret))}.")
            else:
                say("Not stored yet. This is the one step for your own terminal, because the token must never")
                say("pass through Claude. Run this there (PowerShell on Windows, Terminal on Mac):")
                say(f"  {launcher_command('token')}")
        else:
            stored = set_secret(repo, auth)
        step(6, "First run")
        if NONINTERACTIVE and not stored:
            say("The first run starts once the token is stored: run setup again then (/star-triage:setup does it).")
        elif never_ran or STEP_BY_STEP:
            first_run(repo)
        else:
            say("The workflow has run before, so no first run is started. " + actions_hint(repo).split(" If ")[0])
    else:
        manual_steps(repo)

    step(7, "Timers and automatic installs on this computer")
    timers()
    automatic_installs()
    plugin_updates()

    if config_on(read_simple_config(CONFIG_PATH), "obsidian"):
        step(8, "Reading your notes in Obsidian")
        say(f"You need Obsidian 1.9 or newer (the table views use Bases). Open this folder as a vault: {ROOT}")
        say("Then open tracker/Star Triage.base. Notes arrive with `git pull`, after the first run has")
        say("finished and its pull request is merged. For new notes to appear on their own, install the")
        say("community plugin 'Git' (by Vinzent) and set 'Auto pull interval' to 10 minutes.")
    else:
        step(8, "Reading your reviews")
        say("Your reviews, newest first: /star-triage:list in Claude Code, or python star-triage.py list")
        say("(--waiting: only what needs you). Approve one: python star-triage.py approve owner/name")

    step(9, "Final check")
    doctor.report(doctor.check_local())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nStopped. Run it again any time; finished steps are skipped.")
