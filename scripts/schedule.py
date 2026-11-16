"""The two timers: how often GitHub checks for new stars, and how long a reviewed tool waits
before it installs by itself (auto modes only).

    python star-triage.py schedule                    show both and ask (Enter keeps each)
    python star-triage.py schedule --check-every 1    GitHub checks every 1, 3, 6, 12 or 24 hours
    python star-triage.py schedule --install-wait 0   auto modes: wait 0 (no wait), 1, 6 or 24 hours

GitHub reads the check interval only from the cron line in .github/workflows/star-triage.yml.
config.yml keeps the same number as `check_every_hours`, so the doctor can warn when the two
disagree. The wait is `auto_wait_hours` in config.yml. Changed files are committed and pushed,
because the workflow on GitHub reads them from there. Only the Python standard library is needed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import find_data_root, read_mode, read_simple_config, safe_console, wait_hours  # noqa: E402

WORKFLOW_PATH = ".github/workflows/star-triage.yml"
CHECK_CHOICES = {1: "every hour", 3: "every 3 hours", 6: "every 6 hours", 12: "every 12 hours", 24: "once a day"}
WAIT_CHOICES = {0: "no wait", 1: "1 hour", 6: "6 hours", 24: "24 hours"}
RECOMMENDED_CHECK, RECOMMENDED_WAIT = 3, 1
CHECK_QUESTION = "How often should GitHub check for new stars?"
WAIT_QUESTION = "How long should a reviewed tool wait before it installs by itself?"
# An odd minute: GitHub delays or drops scheduled runs at the top of the hour, when most start.
CRON_MINUTE = 17
CHECK_COMMENT = ["# How often GitHub checks for new stars: 1, 3, 6, 12 or 24 hours. GitHub reads it from the",
                 "# cron line in .github/workflows/star-triage.yml; this copy lets the doctor spot the two",
                 "# disagreeing. Change both with: python star-triage.py schedule"]
WAIT_COMMENT = ["# star-triage auto and auto-plus modes only. An item installs by itself once its review is this",
                "# many hours old (0 = no wait). Change it with: python star-triage.py schedule"]
# Files may have CRLF line endings (a Windows checkout): patterns stop before the \r and keep it.
CRON_LINE = re.compile(r"^(?P<lead>[ \t]*-[ \t]*cron:[ \t]*)(?P<q>[\"']?)(?P<cron>[^\"'\r\n#]+?)(?P=q)[ \t]*(?=\r?$)",
                       re.M)
CRON_COMMENT = re.compile(r"^(?P<lead>[ \t]*# )(?:Every [^\r\n(]*|Once a day[^\r\n(]*)(?= \(UTC\))", re.M)


# ----------------------------------------------------------------- the cron line

def cron_for(hours: int) -> str:
    """A valid cron for each choice, always at minute 17 (UTC)."""
    if hours not in CHECK_CHOICES:
        raise ValueError(f"check interval must be one of {', '.join(map(str, CHECK_CHOICES))} hours")
    if hours == 1:
        return f"{CRON_MINUTE} * * * *"
    if hours == 24:
        return f"{CRON_MINUTE} 0 * * *"
    return f"{CRON_MINUTE} */{hours} * * *"


def hours_from_cron(cron: str) -> int | None:
    """The interval in hours of a simple cron (every N hours, or once a day), else None."""
    m = re.fullmatch(r"\d{1,2}\s+(\S+)\s+\*\s+\*\s+\*", cron.strip())
    if not m:
        return None
    hour = m.group(1)
    if hour == "*":
        return 1
    step = re.fullmatch(r"(?:\*|0)/(\d{1,2})", hour)
    if step and 1 <= int(step.group(1)) <= 24:
        return int(step.group(1))
    return 24 if re.fullmatch(r"\d{1,2}", hour) else None


def read_cron(text: str) -> str | None:
    m = CRON_LINE.search(text)
    return m.group("cron").strip() if m else None


def set_cron_text(text: str, hours: int) -> str:
    """The workflow's text with its cron line (and the comment above it) set to `hours`."""
    cron = cron_for(hours)
    text, n = CRON_LINE.subn(lambda m: f'{m.group("lead")}"{cron}"', text, count=1)
    if not n:
        raise ValueError("no `- cron:` line in the workflow file")
    return CRON_COMMENT.sub(lambda m: m.group("lead") + CHECK_CHOICES[hours].capitalize(), text, count=1)


def workflow_hours(root: Path) -> tuple[int | None, str | None]:
    """(hours, cron) from the data repo's workflow. hours is None for a cron that isn't one of
    the simple forms; both are None when the file or the line is missing."""
    try:
        cron = read_cron((root / WORKFLOW_PATH).read_bytes().decode("utf-8", "replace"))
    except OSError:
        return None, None
    return (hours_from_cron(cron) if cron else None), cron


def write_cron(root: Path, hours: int) -> bool:
    """Set the workflow's cron to `hours`. True when the file changed. Line endings are kept."""
    wf = root / WORKFLOW_PATH
    old = wf.read_bytes().decode("utf-8")
    new = set_cron_text(old, hours)
    if new == old:
        return False
    wf.write_bytes(new.encode("utf-8"))
    return True


# ----------------------------------------------------------------- config.yml

def number_in(text: str, key: str) -> int | None:
    m = re.search(rf"^{key}:[ \t]*['\"]?(\d+)['\"]?[ \t]*(?:#[^\r\n]*)?\r?$", text, re.M)
    return int(m.group(1)) if m else None


def set_number(text: str, key: str, value: int, comment: list[str], anchors: tuple[str, ...]) -> str:
    """config.yml's text with `key: value`. A missing key is added with its comment, above the
    first anchor key found (and that key's comment), else at the end. Other lines are kept."""
    if re.search(rf"^{key}:.*$", text, re.M):
        return re.sub(rf"^{key}:[^\r\n]*", f"{key}: {value}", text, count=1, flags=re.M)
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    for anchor in anchors:
        at = next((i for i, line in enumerate(lines) if line.startswith(f"{anchor}:")), None)
        if at is not None:
            while at > 0 and lines[at - 1].startswith("#"):
                at -= 1
            lines[at:at] = comment + [f"{key}: {value}", ""]
            return nl.join(lines) + nl
    return text.rstrip("\r\n") + nl + nl + nl.join(comment + [f"{key}: {value}"]) + nl


def set_check_text(text: str, hours: int) -> str:
    cron_for(hours)  # refuses a value without a valid cron
    return set_number(text, "check_every_hours", hours, CHECK_COMMENT, ("model", "marketplace_name", "contexts"))


def set_wait_text(text: str, hours: int) -> str:
    return set_number(text, "auto_wait_hours", hours, WAIT_COMMENT, ("obsidian", "auto_update", "contexts"))


def wait_ok(value: str) -> bool:
    return value.isdigit() and 0 <= int(value) <= 168


def mismatch(root: Path, cfg: dict) -> str | None:
    """Why config.yml's check_every_hours and the workflow's cron disagree, or None."""
    try:
        want = int(str(cfg.get("check_every_hours", "")).strip())
    except ValueError:
        return None
    hours, cron = workflow_hours(root)
    if cron is None or hours == want:
        return None
    actual = f"every {hours} hours" if hours else f"on the cron \"{cron}\""
    return (f"config.yml says check_every_hours: {want}, but the workflow checks {actual} "
            f"(GitHub goes by the workflow)")


# ----------------------------------------------------------------- what to show

def check_words(hours: int | None) -> str:
    return CHECK_CHOICES.get(hours or 0) or (f"every {hours} hours" if hours else "on a custom schedule")


def summary(root: Path, cfg: dict) -> list[str]:
    """Both timers, their numbers, and where each is set."""
    hours, cron = workflow_hours(root)
    mode = read_mode(cfg)
    wait = wait_hours(cfg)
    lines = [f"Reviews:  GitHub checks for new stars {check_words(hours)}"
             + (f" (cron \"{cron}\", UTC)" if cron and not hours else "")
             + ", in .github/workflows/star-triage.yml. A check with nothing new stops before Claude runs."]
    if mode == "manual":
        lines.append("Installs: manual mode: what you approve installs the next time Claude Code starts.")
        lines.append(f"          (In the auto modes a reviewed tool would wait {wait} h first: auto_wait_hours.)")
    else:
        when = "right after its review" if wait == 0 else f"{wait} h after its review"
        lines.append(f"Installs: {mode} mode: a reviewed tool installs by itself {when}, the next time Claude "
                     "Code starts (auto_wait_hours in config.yml). Right away: /star-triage:sync now")
    return lines


# ----------------------------------------------------------------- commit and push

def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def publish(root: Path, files: list[str], message: str) -> bool:
    """Commit `files` and push. Returns False, after saying what to do, if GitHub lacks them."""
    if git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        print(f"{root} is not a git repo, so the change is only on this computer.")
        return False
    if git(root, "status", "--porcelain", "--", *files).stdout.strip():
        res = git(root, "commit", "-q", "-m", message, "--", *files)
        if res.returncode != 0:
            print("The files are changed, but the commit failed:\n  " + (res.stderr.strip() or res.stdout.strip()))
            print(f"Fix that, then run in {root}:\n  git commit -m \"{message}\" -- {' '.join(files)}\n  git push")
            return False
    res = git(root, "push")
    if res.returncode == 0:
        return True
    raw = res.stderr.strip() or res.stdout.strip()
    err = raw.lower()
    print("The change is committed on this computer, but git push failed, so GitHub still has the old schedule.")
    if "workflow" in err and "scope" in err:
        fix = "gh auth refresh -s workflow\n  git push"
    elif any(t in err for t in ("non-fast-forward", "fetch first", "rejected")):
        fix = "git pull --rebase\n  git push"
    elif any(t in err for t in ("authentication", "could not read username", "permission denied", "terminal prompts")):
        fix = "gh auth setup-git\n  git push"
    elif "no upstream" in err:
        fix = "git push -u origin HEAD"
    else:
        fix = "git push"
        print("  " + raw.replace("\n", "\n  "))
    print(f"Run in {root}:\n  {fix}")
    return False


def apply(root: Path, check: int | None, wait: int | None) -> int:
    config = root / "config.yml"
    text = config.read_bytes().decode("utf-8")  # as it is, line endings included
    new, files, said = text, [], []
    if check is not None:
        new = set_check_text(new, check)
        if write_cron(root, check):
            files.append(WORKFLOW_PATH)
        said.append(f"check {check_words(check)}")
    if wait is not None:
        new = set_wait_text(new, wait)
        said.append("install " + ("with no wait" if wait == 0 else f"{wait} h after the review"))
    if new != text:
        config.write_bytes(new.encode("utf-8"))
        files.append("config.yml")
    if not files:
        print("Nothing to change: that is the schedule already.")
        for line in summary(root, read_simple_config(config)):
            print(line)
        return 0
    message = "star-triage schedule: " + ", ".join(said)
    pushed = publish(root, files, message)
    for line in summary(root, read_simple_config(config)):
        print(line)
    if not pushed:
        print("On this computer only until the push above works.")
    return 0 if pushed else 1


def pick(question: str, choices: dict[int, str], current: int | None, recommended: int) -> int | None:
    """A numbered menu in the terminal. Enter keeps the current value (None)."""
    print(f"\n{question}")
    keys = list(choices)
    for n, value in enumerate(keys, 1):
        tags = [t for t, on in (("recommended", value == recommended), ("current", value == current)) if on]
        print(f"  {n}) {choices[value]}" + (f"  ({', '.join(tags)})" if tags else ""))
    try:
        answer = input(f"Choose 1 to {len(keys)} (Enter keeps the current one): ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.isdigit() and 1 <= int(answer) <= len(keys):
        chosen = keys[int(answer) - 1]
        return None if chosen == current else chosen
    if answer:
        print("Not one of the choices; kept.")
    return None


def flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name:
            return argv[i + 1] if i + 1 < len(argv) else ""
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None) -> int:
    safe_console()
    argv = sys.argv[1:] if argv is None else argv
    root = find_data_root() or Path.cwd()
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not (root / "config.yml").exists():
        print("No star-triage data repo found here. Run this from your data repo (the one with config.yml).")
        return 1
    known = {"--check-every", "--install-wait"}
    unknown = [a for a in argv if a.startswith("-") and a.split("=", 1)[0] not in known]
    check_raw, wait_raw = flag(argv, "--check-every"), flag(argv, "--install-wait")
    problems = [f"Unknown option: {a}" for a in unknown]
    if check_raw is not None and not (check_raw.isdigit() and int(check_raw) in CHECK_CHOICES):
        problems.append("--check-every must be 1, 3, 6, 12 or 24 (hours)")
    if wait_raw is not None and not wait_ok(wait_raw):
        problems.append("--install-wait must be a whole number of hours from 0 to 168 (0 = no wait)")
    if problems:
        print("\n".join(problems))
        return 2
    if check_raw is not None or wait_raw is not None:
        return apply(root, int(check_raw) if check_raw is not None else None,
                     int(wait_raw) if wait_raw is not None else None)

    cfg = read_simple_config(root / "config.yml")
    print("star-triage schedule")
    for line in summary(root, cfg):
        print("  " + line)
    problem = mismatch(root, cfg)
    if problem:
        print(f"  Note: {problem}.")
    # Both ends must be a terminal: on Windows NUL counts as a tty, and inside Claude Code stdout is a pipe.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("\nTo change: python star-triage.py schedule --check-every 1|3|6|12|24  --install-wait 0|1|6|24")
        return 0
    hours, _ = workflow_hours(root)
    check = pick(CHECK_QUESTION, CHECK_CHOICES, hours, RECOMMENDED_CHECK)
    wait = None
    if read_mode(cfg) != "manual":
        wait = pick(WAIT_QUESTION, WAIT_CHOICES, wait_hours(cfg), RECOMMENDED_WAIT)
    if check is None and wait is None:
        print("Kept as it is.")
        return 0
    return apply(root, check, wait)


if __name__ == "__main__":
    sys.exit(main())
