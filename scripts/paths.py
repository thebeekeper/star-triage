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


# The one setting users pick: who approves installs. Shown in this order everywhere.
MODES = {
    "manual": "I approve every install",
    "auto": "Install safe things by themselves (Low risk only)",
    "auto-plus": "Install safe and medium-risk things by themselves (Low and Medium)",
}
MODE_RISKS = {"manual": (), "auto": ("Low",), "auto-plus": ("Low", "Medium")}
MODE_SHORT = {"manual": "you approve every install",
              "auto": "installs Low-risk items by themselves",
              "auto-plus": "installs Low and Medium-risk items by themselves"}
DEFAULT_WAIT_HOURS = 6
DEFAULT_DAILY_LIMIT = 5


def read_mode(cfg: dict) -> str:
    """'manual', 'auto' or 'auto-plus'. The one place the setting is read: `mode` wins; without
    it the older `auto_merge` key counts (notes = manual, low-risk = auto)."""
    raw = str(cfg.get("mode") or "").strip().lower()
    if raw in MODES:
        return raw
    return "auto" if auto_merge_mode(cfg) == "low-risk" else "manual"


def merge_policy(cfg: dict) -> str:
    """What publish.py is told: the mode, or 'never' for an old config that says
    `auto_merge: never` and has no `mode` (those PRs keep waiting, as they always did)."""
    if str(cfg.get("mode") or "").strip().lower() not in MODES and auto_merge_mode(cfg) == "never":
        return "never"
    return read_mode(cfg)


def may_merge(policy: str, risks: list) -> bool:
    """May a review PR merge itself, given the risk of each install it newly approves?"""
    policy = {"notes": "manual", "low-risk": "auto"}.get(policy, policy)  # names used before 5.2
    return policy in MODE_RISKS and all(r in MODE_RISKS[policy] for r in risks)


def auto_number(cfg: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(cfg.get(key, default)).strip())
    except ValueError:
        return default
    return value if low <= value <= high else default


def wait_hours(cfg: dict) -> int:
    return auto_number(cfg, "auto_wait_hours", DEFAULT_WAIT_HOURS, 0, 168)


def daily_limit(cfg: dict) -> int:
    return auto_number(cfg, "auto_daily_limit", DEFAULT_DAILY_LIMIT, 1, 50)


def read_simple_config(path: Path) -> dict:
    """Top-level `key: value` settings of config.yml, read without PyYAML (sync.py and mode.py
    need only the standard library). Lists and nested settings are left out."""
    out = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line or line[0] in " \t#-" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.split(" #")[0].strip().strip("\"'")
        if value and value[0] not in ">|[{":
            out[key.strip()] = value
    return out


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
