#!/usr/bin/env python3
"""Run star-triage commands from this folder:  python3 star-triage.py <command> [options]
(on Windows: python star-triage.py <command>)

    sync      install, update or remove what you approved
              (--dry-run, --status, --uninstall NAME, --prune)
    mode      who approves installs: manual, auto or auto-plus (no word = show a menu)
    pause     stop everything automatic on this computer;  resume  starts it again
    undo      remove the most recent automatic batch and untick its notes
    history   the last 20 automatic installs and removals
    doctor    check your setup and say how to fix anything wrong
    setup     guided setup (safe to run again)

The star-triage code lives in its Claude Code plugin, so it updates by itself; this file only
finds it. Install the plugin once, in Claude Code:

    /plugin marketplace add thebeekeper/star-triage
    /plugin install star-triage@star-triage
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# command -> (script in the plugin, arguments put in front of yours)
COMMANDS = {"sync": ("sync.py", []), "doctor": ("doctor.py", []), "setup": ("setup.py", []),
            "mode": ("mode.py", []), "pause": ("sync.py", ["--pause"]), "resume": ("sync.py", ["--resume"]),
            "undo": ("sync.py", ["--undo"]), "history": ("sync.py", ["--history"])}


def is_engine(path: Path) -> bool:
    try:
        manifest = json.loads((path / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return manifest.get("name") == "star-triage" and (path / "scripts" / "sync.py").exists()


def find_engine() -> Path | None:
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    candidates = []
    if os.environ.get("STAR_TRIAGE_ENGINE"):
        candidates.append(Path(os.environ["STAR_TRIAGE_ENGINE"]))
    try:  # recorded by the plugin each time Claude Code starts
        candidates.append(Path(json.loads((claude / "star-triage.json").read_text(encoding="utf-8"))["engine"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for path in candidates:
        if is_engine(path):
            return path
    found = [p.parent.parent for p in (claude / "plugins").glob("**/scripts/sync.py") if is_engine(p.parent.parent)]
    return max(found, key=lambda p: (p / "scripts" / "sync.py").stat().st_mtime) if found else None


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 0 if len(sys.argv) < 2 else 1
    engine = find_engine()
    if not engine:
        print("The star-triage plugin was not found. Install it in Claude Code:\n"
              "  /plugin marketplace add thebeekeper/star-triage\n"
              "  /plugin install star-triage@star-triage\n"
              "then start Claude Code once and try again.")
        return 1
    name, front = COMMANDS[sys.argv[1]]
    script = engine / "scripts" / name
    if not script.exists():
        print("Your star-triage plugin is older than this command. Update it, in a terminal:\n"
              "  claude plugin marketplace update star-triage\n"
              "  claude plugin update star-triage@star-triage")
        return 1
    env = dict(os.environ, STAR_TRIAGE_DATA=str(HERE))
    return subprocess.call([sys.executable, str(script), *front, *sys.argv[2:]], cwd=HERE, env=env)


if __name__ == "__main__":
    sys.exit(main())
