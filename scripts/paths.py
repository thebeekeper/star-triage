"""Where things are. Standard library only, so every script can import it first.

star-triage has two parts that live in different places:

  ENGINE  this code: the GitHub Action (in CI) or the Claude Code plugin (on your PC).
  DATA    your own repo: config.yml, tracker/ notes, installs.json, the tiny workflow.

The data repo is found in this order:
  1. STAR_TRIAGE_DATA (set by the GitHub Action to the checked-out repo)
  2. the current folder, if it holds config.yml and tracker/
  3. the engine folder itself, if it holds them (the old all-in-one layout)
  4. the folder recorded by setup in ~/.claude/star-triage.json ("linked")
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
LINK_FILE = CLAUDE_DIR / "star-triage.json"
REPO_URL = "https://github.com/thebeekeper/star-triage"
TROUBLESHOOTING_URL = f"{REPO_URL}/blob/main/docs/TROUBLESHOOTING.md"
# One definition of these settings for the pipeline, setup and the doctor.
DEFAULT_MAX_PER_RUN = 8
AUTO_MERGE_NEVER = ("never", "off", "no", "false", "none")
AUTO_MERGE_LOW_RISK = ("low-risk", "low", "lowrisk")


def auto_merge_mode(cfg: dict) -> str:
    """'notes', 'low-risk' or 'never' (see config.yml). YAML reads off/no as False."""
    raw = cfg.get("auto_merge", "notes")
    if raw is False or str(raw).strip().lower() in AUTO_MERGE_NEVER:
        return "never"
    return "low-risk" if str(raw).strip().lower() in AUTO_MERGE_LOW_RISK else "notes"


def safe_console() -> None:
    """Print '?' instead of crashing when the console can't show a character."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def is_data_repo(path: Path) -> bool:
    return (path / "config.yml").is_file() and (path / "tracker").is_dir()


def read_link() -> dict:
    try:
        data = json.loads(LINK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_link(**values) -> None:
    """Record where the data repo and/or the engine are, for the launcher and the hook."""
    data = read_link()
    data.update({k: str(v) for k, v in values.items() if v})
    LINK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINK_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def linked_data_root() -> Path | None:
    """The data repo you chose: STAR_TRIAGE_DATA, or the one setup recorded. Never the current
    folder, so automatic runs (the SessionStart hook) cannot be pointed at a project you opened."""
    env = os.environ.get("STAR_TRIAGE_DATA")
    if env:
        return Path(env).resolve()
    linked = read_link().get("data")
    if linked and isinstance(linked, str) and is_data_repo(Path(linked)):
        return Path(linked)
    return None


def find_data_root() -> Path | None:
    env = os.environ.get("STAR_TRIAGE_DATA")
    if env:
        return Path(env).resolve()
    for cand in (Path.cwd(), ENGINE_DIR):
        if is_data_repo(cand):
            return cand.resolve()
    return linked_data_root()


def running_as_plugin() -> bool:
    """True when this code is the installed Claude Code plugin (its hook is then built in)."""
    parts = [p.lower() for p in ENGINE_DIR.parts]
    if os.environ.get("CLAUDE_PLUGIN_ROOT") or ("plugins" in parts and ".claude" in parts):
        return True
    # A plugin installed from a local folder runs in place; its hook records where it is.
    recorded = read_link().get("engine")
    return bool(recorded) and Path(recorded).resolve() == ENGINE_DIR
