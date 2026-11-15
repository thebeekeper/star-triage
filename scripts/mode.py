"""Choose who approves installs. One setting, three choices.

    python star-triage.py mode             show the current mode and a menu
    python star-triage.py mode auto        switch without the menu (manual, auto or auto-plus)

  manual     I approve every install (default)
  auto       Install safe things by themselves (Low risk only)
  auto-plus  Install safe and medium-risk things by themselves (Low and Medium)

High-risk and blocked items are never installed automatically, in any mode.
It updates `mode` in config.yml, commits "Switch star-triage to <mode>" and pushes, because the
workflow on GitHub reads the setting from there. Only the Python standard library is needed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import (MODES, MODE_SHORT, daily_limit, find_data_root, read_mode,  # noqa: E402
                   read_simple_config, safe_console, wait_hours)

ROOT = find_data_root() or Path.cwd()
CONFIG_PATH = ROOT / "config.yml"
COMMENT = ["# Who approves installs: manual (you approve every install), auto (Low-risk items install",
           "# by themselves) or auto-plus (Low and Medium). High-risk and blocked items always wait for",
           "# you. Change it with: python star-triage.py mode"]


def set_mode_text(text: str, mode: str) -> str:
    """config.yml with `mode` set. A new key goes at the top, under the file's opening comment."""
    if re.search(r"^mode:.*$", text, re.M):
        return re.sub(r"^mode:.*$", f"mode: {mode}", text, count=1, flags=re.M)
    lines = text.splitlines()
    at = 0
    while at < len(lines) and lines[at].startswith("#"):
        at += 1
    block = ([""] if at else []) + COMMENT + [f"mode: {mode}"]
    if at < len(lines) and lines[at].strip():
        block.append("")
    lines[at:at] = block
    return "\n".join(lines) + "\n"


def describe(mode: str, cfg: dict) -> str:
    if mode == "manual":
        return "manual mode: nothing installs until you merge its PR or tick approve."
    return (f"{mode} mode: {MODE_SHORT[mode]}, after a {wait_hours(cfg)} h safety delay, at most "
            f"{daily_limit(cfg)} a day. Pause any time: python star-triage.py pause")


def git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def publish(mode: str) -> bool:
    """Commit and push config.yml. Returns False, after saying what to do, if GitHub lacks it."""
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print(f"{ROOT} is not a git repo, so the change is only on this computer.")
        return False
    if git("status", "--porcelain", "--", "config.yml").stdout.strip():
        res = git("commit", "-q", "-m", f"Switch star-triage to {mode}", "--", "config.yml")
        if res.returncode != 0:
            print("config.yml is changed, but the commit failed:\n  " + (res.stderr.strip() or res.stdout.strip()))
            print(f"Fix that, then run in {ROOT}:\n  git commit -m \"Switch star-triage to {mode}\" -- config.yml\n  git push")
            return False
    res = git("push")
    if res.returncode == 0:
        return True
    err = (res.stderr.strip() or res.stdout.strip()).lower()
    print("The change is committed on this computer, but git push failed, so GitHub still has the old mode.")
    if any(t in err for t in ("non-fast-forward", "fetch first", "rejected")):
        fix = "git pull --rebase\n  git push"
    elif any(t in err for t in ("authentication", "could not read username", "permission denied", "terminal prompts")):
        fix = "gh auth setup-git\n  git push"
    elif "no upstream" in err or "has no upstream branch" in err:
        fix = "git push -u origin HEAD"
    else:
        fix = "git push"
        print("  " + (res.stderr.strip() or res.stdout.strip()).replace("\n", "\n  "))
    print(f"Run in {ROOT}:\n  {fix}")
    return False


def switch(mode: str) -> int:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    cfg = read_simple_config(CONFIG_PATH)
    if read_mode(cfg) == mode and re.search(rf"^mode:\s*['\"]?{mode}['\"]?\s*(#.*)?$", text, re.M):
        pushed = publish(mode)  # a switch that never reached GitHub is finished here
        print(f"star-triage is already in {describe(mode, cfg)}")
        return 0 if pushed else 1
    CONFIG_PATH.write_bytes(set_mode_text(text, mode).encode("utf-8"))
    pushed = publish(mode)
    where = "" if pushed else " On this computer only until the push above works."
    print(f"star-triage is now in {describe(mode, read_simple_config(CONFIG_PATH))}{where}")
    return 0 if pushed else 1


def main() -> int:
    safe_console()
    args = [a.strip().lower() for a in sys.argv[1:]]
    if not CONFIG_PATH.exists():
        print("No star-triage data repo found here. Run this from your data repo (the one with config.yml).")
        return 1
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args:
        if len(args) > 1 or args[0] not in MODES:
            print(f"'{' '.join(args)}' is not a mode. Choose one of: {', '.join(MODES)}")
            return 1
        return switch(args[0])
    current = read_mode(read_simple_config(CONFIG_PATH))
    print(f"Mode: {current}\n")
    names = list(MODES)
    for n, name in enumerate(names, 1):
        print(f"  {n}) {name + ':':11}{MODES[name]}{'  (current)' if name == current else ''}")
    print("\nHigh-risk and blocked items are never installed automatically, in any mode.")
    if not sys.stdin.isatty():
        print("To switch: python star-triage.py mode <" + " | ".join(names) + ">")
        return 0
    try:
        pick = input("Pick 1, 2 or 3 (Enter keeps the current mode): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        pick = ""
    if not pick:
        print(f"Kept {current} mode.")
        return 0
    chosen = names[int(pick) - 1] if pick in ("1", "2", "3") else pick if pick in MODES else None
    if not chosen:
        print("That is not 1, 2 or 3. Nothing was changed.")
        return 1
    return switch(chosen)


if __name__ == "__main__":
    sys.exit(main())
