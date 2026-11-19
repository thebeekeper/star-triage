"""Install what you approved on this computer: plugins, skills, MCP servers and CLI tools.

    python star-triage.py sync                 pull, then install, update or remove to match your approvals
    python star-triage.py sync --dry-run       show the plan, change nothing
    python star-triage.py sync --now           install what is approved now, without the safety delay
                                               (the daily limit and the High-risk and blocked rules stay)
    python star-triage.py sync --status        everything reviewed that can be installed, and its state here
    python star-triage.py sync --uninstall X   remove one (owner/name or its name) and untick its approve box
    python star-triage.py pause | resume       stop or restart everything automatic on this computer
    python star-triage.py undo                 remove the most recent automatic batch and untick its notes
    python star-triage.py history              the last 20 automatic installs and removals

Automatic mode: the star-triage Claude Code plugin runs this in the background whenever
Claude Code starts (its SessionStart hook calls `sync.py --hook`). Without the plugin,
`--install-hook` adds the same hook to your settings.json, and `--remove-hook` takes it out.

What counts as approved
  - Items the workflow approved (verdict Install, not High risk, nothing blocking), once you
    merged the PR. They arrive in installs.json, pinned to an exact commit or version.
  - Anything you tick `approve` on in Obsidian. Untick it and the item is removed again.
  - Blocked items (high-severity scan findings, binaries) are never installed in the
    background. Run this script by hand to confirm one.

Auto mode (`mode: auto` or `auto-plus` in config.yml, see mode.py): the background run installs
an item only once its review is auto_wait_hours old (1 in new configs; 6 when the line is
missing; time to untick it or pause)
and while fewer than auto_daily_limit (default 5) were installed today. High-risk and blocked
items are never installed in the background. Every automatic action goes to
.star-triage/history.jsonl, which `history` and `undo` read. `--now` follows the same rules
except the safety delay, and is recorded the same way.

Plugin updates: when installs.json says your reviews come from a newer star-triage than this
plugin, the hook runs `claude plugin marketplace update star-triage` and `claude plugin update
star-triage@star-triage` in the background, at most once a day (`auto_update: false` in
config.yml turns it off).

It never installs anything that is not in installs.json, never passes --yes, and never
copies your API keys anywhere: an MCP server that needs a key is listed with the command
to add it yourself. Everything it does is logged to .star-triage/sync.log.
Only the Python standard library is needed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor import UPDATE_COMMANDS, UPDATE_HOW, engine_mismatch, find_claude  # noqa: E402  (standard library only)

from paths import (ENGINE_DIR, MODE_SHORT, NOW_HINT, TROUBLESHOOTING_URL, config_on, daily_limit,  # noqa: E402
                   find_data_root, install_launcher, launcher_command, linked_data_root, read_mode,
                   read_simple_config, running_as_plugin, safe_console, wait_hours, write_link)

# Automatic runs use only the data repo you linked (setup, or STAR_TRIAGE_DATA), never the
# folder Claude Code happens to be opened in: that folder may be someone else's project.
AUTOMATIC = bool({"--hook", "--background", "--self-update"} & set(sys.argv[1:]))
LINKED = linked_data_root() if AUTOMATIC else None
ROOT = (LINKED if AUTOMATIC else find_data_root()) or Path.cwd()  # your data repo (see paths.py)
INSTALLS_PATH = ROOT / "installs.json"
MARKET_PATH = ROOT / ".claude-plugin" / "marketplace.json"
STATE_DIR = ROOT / ".star-triage"
STATE_PATH = STATE_DIR / "installed.json"
LOG_PATH = STATE_DIR / "sync.log"
RESULT_PATH = STATE_DIR / "last-result.json"
HOOK_STAMP = STATE_DIR / "hook.json"
LOCK_PATH = STATE_DIR / "sync.lock"
PAUSE_PATH = STATE_DIR / "paused"          # present = nothing automatic happens on this computer
HISTORY_PATH = STATE_DIR / "history.jsonl"  # one line per automatic install or removal
SELF_UPDATE_PATH = STATE_DIR / "self-update.json"  # the plugin's last attempt to update itself
SELF_UPDATE_EVERY = 24 * 3600
PLUGIN_MARKETPLACE = "star-triage"
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
SKILLS_DIR = CLAUDE_DIR / "skills"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
GUIDE = TROUBLESHOOTING_URL
HOOK_MARK = "star-triage"
HOOK_EVERY_SECONDS = 5 * 60
MAX_SKILL_BYTES = 20 * 1024 * 1024
MAX_TAR_MEMBERS = 50_000
SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


# ----------------------------------------------------------------- small helpers

def log(msg: str) -> None:
    if AUTOMATIC and not LINKED:
        return  # no data repo to log into; never write into the current folder
    STATE_DIR.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def settings() -> tuple[str, int, int]:
    """(mode, hours an item waits after its review, installs allowed per day) from config.yml."""
    cfg = read_simple_config(ROOT / "config.yml")
    return read_mode(cfg), wait_hours(cfg), daily_limit(cfg)


def history_read() -> list[dict]:
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("key"):
            rows.append(row)
    return rows


def history_add(rows: list[dict]) -> None:
    if not rows:
        return
    STATE_DIR.mkdir(exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class Runner:
    """Runs commands. interactive=True: you see output and can answer questions."""

    def __init__(self, dry: bool, interactive: bool):
        self.dry, self.interactive = dry, interactive

    def __call__(self, cmd: list[str], label: str | None = None, timeout: int = 900) -> bool:
        shown = " ".join(label.split() if label else [Path(cmd[0]).stem, *cmd[1:]])
        print(f"  > {shown}")
        if self.dry:
            return True
        exe = shutil.which(cmd[0]) or cmd[0]
        try:
            if self.interactive:
                code = subprocess.run([exe, *cmd[1:]], cwd=ROOT, timeout=timeout).returncode
                out = ""
            else:
                res = subprocess.run([exe, *cmd[1:]], cwd=ROOT, timeout=timeout, stdin=subprocess.DEVNULL,
                                     capture_output=True, text=True, encoding="utf-8", errors="replace")
                code, out = res.returncode, (res.stdout + res.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, out = 127, str(exc)
        log(f"{'ok ' if code == 0 else 'ERR'} {shown} (exit {code}){(' :: ' + out[-300:]) if code and out else ''}")
        if code and out and not self.interactive:
            print("    " + out.splitlines()[-1][:200])
        return code == 0


# ----------------------------------------------------------------- approvals

FRONT = re.compile(r"\A---\s*\n(.*?)\n---", re.S)


def note_approve(note: str) -> bool | None:
    """The approve checkbox on a note, read without PyYAML. None if not set."""
    try:
        text = (ROOT / note).read_text(encoding="utf-8")
    except OSError:
        return None
    m = FRONT.match(text)
    hit = re.search(r"^approve:\s*(\S+)\s*$", m.group(1), re.M) if m else None
    if not hit:
        return None
    return hit.group(1).strip("\"'").lower() in ("true", "yes")


def set_note_approve(note: str, value: bool) -> None:
    path = ROOT / note
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    m = FRONT.match(text)
    if not m:
        return
    block = m.group(1)
    line = f"approve: {'true' if value else 'false'}"
    block = re.sub(r"^approve:.*$", line, block, flags=re.M) if re.search(r"^approve:", block, re.M) \
        else block + "\n" + line
    path.write_text(text[:m.start(1)] + block + text[m.end(1):], encoding="utf-8")


def fingerprint(item: dict) -> str:
    if item["kind"] in ("plugin", "skill"):
        return item.get("commit", "")
    return json.dumps(item.get("spec", {}), sort_keys=True)


def wanted(items: dict) -> dict[str, dict]:
    """Items that should be installed here, with the reason they are approved."""
    out = {}
    for key, item in items.items():
        if item.get("kind") not in ("plugin", "skill", "mcp", "cli"):
            continue
        ticked = note_approve(item.get("note", ""))
        approved = item.get("approved", False) if ticked is None else ticked
        if approved:
            out[key] = item
    return out


# ----------------------------------------------------------------- installers

def skill_target(name) -> Path | None:
    """SKILLS_DIR/<name>, or None unless the name is plain and lands directly in the skills folder."""
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
        return None
    target = (SKILLS_DIR / name).resolve()
    return target if target.parent == SKILLS_DIR.resolve() else None


def unsafe_member(name: str) -> bool:
    """A tar member name that could leave the folder it is unpacked into (also on Windows)."""
    parts = name.split("/")
    return name.startswith("/") or "\\" in name or ".." in parts or any(":" in p for p in parts)


class Sync:
    def __init__(self, run: Runner, claude: str | None, scope: str):
        self.run, self.claude, self.scope = run, claude, scope
        self.market_ready = False
        self.notes: list[str] = []

    # plugins -----------------------------------------------------------
    def _market(self) -> str | None:
        market = load_json(MARKET_PATH, None)
        if not market or not self.claude:
            return None
        if not self.market_ready:
            self.run([self.claude, "plugin", "marketplace", "add", str(ROOT)])  # no-op if already added
            self.run([self.claude, "plugin", "marketplace", "update", market["name"]])
            self.market_ready = True
        return market["name"]

    def plugin(self, item: dict, action: str, old: dict | None) -> dict | None:
        mk = self._market()
        if not mk:
            self.notes.append(f"{item['repo']}: Claude Code CLI or marketplace missing")
            return None
        name = item["spec"]["plugin"]
        listed = {p.get("name") for p in load_json(MARKET_PATH, {}).get("plugins", [])}
        if action != "remove" and name not in listed:
            self.notes.append(f"{item['repo']}: plugin '{name}' is not in your marketplace list yet. "
                              "It is added by the next workflow run; then sync again.")
            return None
        cmd = {"install": ["install", f"{name}@{mk}", "--scope", self.scope],
               "update": ["update", f"{name}@{mk}"],
               "remove": ["uninstall", f"{name}@{mk}"]}[action]
        return {"name": name} if self.run([self.claude, "plugin", *cmd]) else None

    # skills ------------------------------------------------------------
    def skill(self, item: dict, action: str, old: dict | None) -> dict | None:
        owned = {n for n in (old or {}).get("skills", []) if skill_target(n)}
        if action == "remove":
            for name in owned:
                target = skill_target(name)
                print(f"  > remove {target}")
                if not self.run.dry and target.is_dir():
                    shutil.rmtree(target)
                    log(f"ok  removed skill {name}")
            return {}
        skills = []
        for sk in item["spec"]["skills"]:
            if isinstance(sk, dict) and skill_target(sk.get("name")) and isinstance(sk.get("path"), str):
                skills.append(sk)
            else:
                bad = sk.get("name") if isinstance(sk, dict) else sk
                self.notes.append(f"{item['repo']}: skill name {str(bad)[:80]!r} is not allowed; skipped")
        if not skills:
            return None
        print(f"  > download {item['repo']}@{item['commit'][:12]} and copy {len(skills)} skill(s) to {SKILLS_DIR}")
        if self.run.dry:
            return {"skills": [s["name"] for s in skills]}
        url = f"https://codeload.github.com/{item['repo']}/tar.gz/{item['commit']}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "star-triage"}),
                                        timeout=120) as resp:
                data = resp.read(MAX_SKILL_BYTES * 3 + 1)
        except OSError as exc:
            log(f"ERR download {url}: {exc}")
            self.notes.append(f"{item['repo']}: download failed ({exc})")
            return None
        try:
            installed = self._unpack_skills(data, item, skills, owned)
        except (tarfile.TarError, OSError, EOFError) as exc:
            log(f"ERR unpack {item['repo']}: {exc}")
            self.notes.append(f"{item['repo']}: the download could not be unpacked safely ({exc}); skipped")
            return None
        for name in owned - set(installed) - {s["name"] for s in skills}:
            shutil.rmtree(skill_target(name), ignore_errors=True)  # dropped in a newer review
        return {"skills": installed} if installed else None

    def _unpack_skills(self, data: bytes, item: dict, skills: list[dict], owned: set[str]) -> list[str]:
        """Copy each skill folder out of the repo tarball. Nothing is written outside SKILLS_DIR."""
        installed = []
        with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = []
            for m in tar:
                members.append(m)
                if len(members) > MAX_TAR_MEMBERS:
                    raise tarfile.TarError(f"more than {MAX_TAR_MEMBERS} files")
            for sk in skills:
                name, path = sk["name"], sk["path"].strip("/")
                target = skill_target(name)
                if target.exists() and name not in owned:
                    self.notes.append(f"skill '{name}' already exists in {SKILLS_DIR} and is not from star-triage; skipped")
                    continue
                stage, total, unsafe = (Path(tmp) / name).resolve(), 0, ""
                for m in members:
                    parts = m.name.split("/")[1:]  # drop the "<repo>-<sha>/" top folder
                    rel = parts if path == "." else (parts[len(path.split("/")):]
                                                     if parts[:len(path.split("/"))] == path.split("/") else None)
                    if not rel or not m.isfile() or rel[0] in (".git", ".github"):
                        continue
                    dest = stage.joinpath(*rel).resolve()
                    if unsafe_member(m.name) or stage not in dest.parents:
                        unsafe = m.name
                        break
                    total += m.size
                    if total > MAX_SKILL_BYTES:
                        break
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(m).read())
                if unsafe:
                    log(f"ERR skill {name}: unsafe file name {unsafe[:200]!r}")
                    self.notes.append(f"skill '{name}': the download has an unsafe file name; skipped")
                    continue
                if not (stage / "SKILL.md").exists() or total > MAX_SKILL_BYTES:
                    self.notes.append(f"skill '{name}': not found in the download, or too large; skipped")
                    continue
                if target.exists():
                    shutil.rmtree(target)
                SKILLS_DIR.mkdir(parents=True, exist_ok=True)
                shutil.move(str(stage), str(target))
                installed.append(name)
                log(f"ok  skill {name} from {item['repo']}@{item['commit'][:12]}")
        return installed

    # MCP servers -------------------------------------------------------
    def mcp(self, item: dict, action: str, old: dict | None) -> dict | None:
        if not self.claude:
            self.notes.append(f"{item['repo']}: Claude Code CLI not found")
            return None
        name, spec = item["name"], item["spec"]
        if action in ("remove", "update"):
            self.run([self.claude, "mcp", "remove", (old or {}).get("name", name), "--scope", "user"])
            if action == "remove":
                return {}
        elif not self.run.dry:
            exists = subprocess.run([shutil.which(self.claude) or self.claude, "mcp", "get", name],
                                    capture_output=True, stdin=subprocess.DEVNULL).returncode == 0
            if exists:
                self.notes.append(f"MCP server '{name}' already exists and is not from star-triage; skipped")
                return None
        needs = [e["name"] for e in spec.get("env", []) if e.get("required")]
        if spec.get("transport") == "http":
            cmd = [self.claude, "mcp", "add", "--transport", "http", "--scope", "user", name, spec["url"]]
        else:
            runner = "npx" if spec["registry"] == "npm" else "uvx"
            if not shutil.which(runner) and not self.run.dry:
                self.notes.append(f"{item['repo']}: needs {runner} "
                                  f"({'install Node.js' if runner == 'npx' else 'install uv'}), then sync again")
                return None
            cmd = [self.claude, "mcp", "add", "--transport", "stdio", "--scope", "user", name, "--",
                   *launch_command(spec)]
        if needs:
            env = " ".join(f"--env {n}=<your key>" for n in needs)
            self.notes.append(f"MCP server '{name}' needs {', '.join(needs)}. Add it yourself with your key: "
                              + " ".join(cmd[:3]) + f" {env} " + " ".join(cmd[3:]))
            return None
        return {"name": name} if self.run(cmd) else None

    # command-line tools ------------------------------------------------
    def cli(self, item: dict, action: str, old: dict | None) -> dict | None:
        spec = item["spec"]
        pkg, ver = spec["package"], spec["version"]  # pinned by the workflow
        if spec["registry"] == "npm":
            if not shutil.which("npm") and not self.run.dry:
                self.notes.append(f"{item['repo']}: needs npm (install Node.js), then sync again")
                return None
            cmd = ["npm", "uninstall", "-g", pkg] if action == "remove" else ["npm", "install", "-g", f"{pkg}@{ver}"]
        else:
            tool = "uv" if shutil.which("uv") else "pipx" if shutil.which("pipx") else None
            tool = (old or {}).get("via", tool) if action == "remove" else tool
            if not tool and not self.run.dry:
                self.notes.append(f"{item['repo']}: needs uv (recommended) or pipx, then sync again")
                return None
            tool = tool or "uv"
            if action == "remove":
                cmd = [tool, "tool", "uninstall", pkg] if tool == "uv" else [tool, "uninstall", pkg]
            else:
                cmd = [tool, "tool", "install", pypi_requirement(spec)] if tool == "uv" else \
                      [tool, "install", "--force", pypi_requirement(spec)]
        if not self.run(cmd):
            return None
        if action == "remove":
            return {}
        setup = spec.get("setup")
        if setup and action == "install":
            # Only the tool's own command, found only where the tool was just installed: never
            # whatever else on PATH happens to have that name.
            safe = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", setup[0])
            exe = self._tool_bin(spec, setup[0], cmd[0]) if safe else None
            if not exe and not self.run.dry:
                self.notes.append(f"{item['repo']}: installed, but '{setup[0]}' was not found where the tool "
                                  f"was installed. If you trust it, run it yourself: {' '.join(setup)}")
            elif not self.run([exe or setup[0], *setup[1:]], label=" ".join(setup)):
                self.notes.append(f"{item['repo']}: installed, but `{' '.join(setup)}` failed. Run it yourself.")
        return {"via": cmd[0]}

    @staticmethod
    def _tool_bin(spec: dict, name: str, via: str = "uv") -> str | None:
        """The command `name` inside the folder this tool's commands were installed into."""
        try:
            if via == "pipx":
                folder = Path(subprocess.run([shutil.which("pipx") or "pipx", "environment", "--value", "PIPX_BIN_DIR"],
                                             capture_output=True, text=True, timeout=30).stdout.strip())
            elif spec["registry"] == "npm":
                prefix = subprocess.run([shutil.which("npm") or "npm", "prefix", "-g"],
                                        capture_output=True, text=True, timeout=30).stdout.strip()
                folder = Path(prefix) if os.name == "nt" else Path(prefix) / "bin"
            else:
                folder = Path(subprocess.run([shutil.which("uv") or "uv", "tool", "dir", "--bin"],
                                             capture_output=True, text=True, timeout=30).stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            return None
        if not folder.is_absolute():  # the lookup printed nothing: don't fall back to the current folder
            return None
        for cand in (folder / name, folder / f"{name}.exe", folder / f"{name}.cmd"):
            if cand.exists():
                return str(cand)
        return None


# ----------------------------------------------------------------- planning and running

def pypi_requirement(spec: dict) -> str:
    extras = f"[{','.join(spec['extras'])}]" if spec.get("extras") else ""
    return f"{spec['package']}{extras}=={spec['version']}"


def launch_command(spec: dict) -> list[str]:
    """How a stdio MCP server is started. Kept identical to queue_installs.py."""
    if spec["registry"] == "npm":
        return ["npx", "-y", f"{spec['package']}@{spec['version']}", *spec.get("args", [])]
    command = spec.get("command") or spec["package"]
    return ["uvx", "--from", pypi_requirement(spec), command, *spec.get("args", [])]


def plan(items: dict, state: dict, prune: bool) -> list[tuple[str, str, dict]]:
    steps = []
    for key, item in sorted(wanted(items).items()):
        old = state.get(key)
        if not old:
            steps.append(("install", key, item))
        elif old.get("fp") != fingerprint(item):
            steps.append(("update", key, item))
    approved = wanted(items)
    for key, old in sorted(state.items()):
        if key in approved or "kind" not in old:
            continue
        if key in items:  # still reviewed, but you unticked approve
            steps.append(("remove", key, items[key]))
        elif prune:
            steps.append(("remove", key, {"repo": key, "kind": old["kind"], "name": old.get("name", ""),
                                          "spec": old.get("spec", {}), "note": ""}))
    return steps


def reviewed_time(item: dict) -> dt.datetime | None:
    """When the item's review finished (UTC), or None when installs.json does not say."""
    for field in ("reviewed_at", "reviewed"):
        try:
            when = dt.datetime.fromisoformat(str(item.get(field) or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
    return None


def hold_back(steps: list, wait_h: int, limit: int, history: list[dict], now: dt.datetime | None = None):
    """Auto modes, background only. Splits the plan into (run now, waiting for the safety delay,
    over today's limit, High risk). Removals never wait. Blocked items go through to execute(),
    which refuses them in the background as it always did."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = dt.date.today().isoformat()
    used = sum(1 for h in history if h.get("auto") and h.get("action") in ("installed", "updated")
               and str(h.get("at", ""))[:10] == today)
    run, waiting, capped, high = [], [], [], []
    for step in steps:
        action, _, item = step
        when = reviewed_time(item)
        if action == "remove" or item.get("status") == "blocked":
            run.append(step)
        elif item.get("risk") == "High":
            high.append(step)
        elif when and now - when < dt.timedelta(hours=wait_h):
            waiting.append(step)
        elif used >= limit:
            capped.append(step)
        else:
            used += 1
            run.append(step)
    return run, waiting, capped, high


def short_name(key: str, item: dict) -> str:
    return str(item.get("name") or item.get("spec", {}).get("plugin") or key.rsplit("/", 1)[-1])


def local_time(when: dt.datetime, now: dt.datetime | None = None) -> str:
    """'11:52' if it is today on this computer, else 'Tue 11:52' (local time)."""
    local = when.astimezone()
    today = (now or dt.datetime.now(dt.timezone.utc)).astimezone().date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%a %H:%M")


def names_and_more(names: list[str], most: int = 3) -> str:
    return ", ".join(names[:most]) + (f" and {len(names) - most} more" if len(names) > most else "")


def and_join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def waiting_text(waiting: list, wait_h: int, now: dt.datetime | None = None, most: int = 3) -> str:
    """'ponytail and brag install after 18:58, ccusage after 19:40 (1 h safety delay)': items
    that share a time are named together (UT-18, UT-24). At most `most` names; the rest counted."""
    groups: dict[str, list[str]] = {}
    for _, k, i in sorted(waiting, key=lambda s: reviewed_time(s[2])):
        when = local_time(reviewed_time(i) + dt.timedelta(hours=wait_h), now)
        groups.setdefault(when, []).append(short_name(k, i))
    bits, shown, hidden = [], 0, 0
    for when, names in groups.items():
        room = most - shown
        if room <= 0:
            hidden += len(names)
            continue
        take = names[:room]
        shown += len(take)
        label = take + ([f"{len(names) - len(take)} more"] if len(names) > len(take) else [])
        verb = "" if bits else ("installs " if len(names) == 1 else "install ")
        bits.append(f"{and_join(label)} {verb}after {when}")
    more = f" and {hidden} more" if hidden else ""
    return ", ".join(bits) + more + f" ({wait_h} h safety delay)"


def why(action: str, item: dict, old: dict | None) -> str:
    """The reason written to the history for one step."""
    old = old or {}
    if action == "remove":
        if not item.get("note") and "approved" not in item:
            return "removed: no longer in installs.json"
        downgraded = not item.get("approved") and (
            old.get("by") == "workflow" or ("by" not in old and old.get("fp") != fingerprint(item)))
        return "removed: downgraded by re-review" if downgraded else "removed: approve unticked"
    how = "approved by the review" if item.get("approved") else "you ticked approve"
    return f"{'new reviewed version' if action == 'update' else 'installed'}: {item.get('risk') or 'unknown'} risk, {how}"


def describe(item: dict) -> str:
    spec = item.get("spec", {})
    return {"plugin": f"plugin {spec.get('plugin', '')}",
            "skill": "skill " + ", ".join(s["name"] for s in spec.get("skills", [])[:4]),
            "mcp": f"MCP server {item.get('name', '')}",
            "cli": f"{spec.get('package', '')} {spec.get('version', '')}"}.get(item["kind"], item["kind"])


def execute(steps, state: dict, sync: Sync, confirm_blocked: bool) -> dict:
    result = {"at": dt.datetime.now().isoformat(timespec="seconds"), "done": [], "failed": [], "skipped": [],
              "log": []}
    for action, key, item in steps:
        label = f"{action} {key}: {describe(item)}"
        if action != "remove" and item.get("status") == "blocked":
            if not confirm_blocked:
                result["skipped"].append(f"{key} is blocked ({item.get('blocked')}); "
                                         "run `python star-triage.py sync` yourself to confirm it")
                continue
            print(f"\n{key} is BLOCKED by the security scan: {item.get('blocked')}.")
            try:
                answer = input("Install it anyway? Type 'install' to confirm: ").strip().lower()
            except EOFError:
                answer = ""
            if answer != "install":
                result["skipped"].append(f"{key}: not confirmed")
                continue
        print(f"\n{label}")
        handler = getattr(sync, item["kind"])
        reason = why(action, item, state.get(key))
        out = handler(item, action, state.get(key))
        if out is None:
            result["failed"].append(label)
            continue
        result["done"].append(label)
        if sync.run.dry:
            continue
        result["log"].append({"action": {"install": "installed", "update": "updated", "remove": "removed"}[action],
                              "key": key, "name": short_name(key, item), "what": describe(item), "why": reason})
        if action == "remove":
            state.pop(key, None)
        else:
            state[key] = {"kind": item["kind"], "name": item.get("name", ""), "fp": fingerprint(item),
                          "spec": item.get("spec", {}), "at": dt.date.today().isoformat(),
                          "by": "workflow" if item.get("approved") else "tick", **out}
        save_json(STATE_PATH, state)
    result["notes"] = sync.notes
    return result


def pull(quiet: bool) -> bool:
    """Bring the latest notes and approvals from GitHub. False when that failed (the local
    copy is used then); True when it worked or there is no git repo to pull."""
    if not (ROOT / ".git").exists():
        return True
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(["git", "pull", "--ff-only", "--autostash", "-q"], cwd=ROOT, env=env,
                             capture_output=quiet, timeout=30, stdin=subprocess.DEVNULL)
        if res.returncode:
            log("git pull failed; using the approvals already on this computer")
            return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        log("git pull timed out; using the approvals already on this computer")
        return False


def read_scope() -> str:
    try:
        m = re.search(r"^install_scope:\s*['\"]?(\w+)", (ROOT / "config.yml").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m and m.group(1) in ("user", "project", "local") else "user"
    except OSError:
        return "user"


def needs_restart(result: dict) -> bool:
    return any((" plugin " in d or "MCP server" in d) and not d.startswith("remove") for d in result["done"])


# ----------------------------------------------------------------- automatic mode (SessionStart hook)

def hook_command() -> str:
    py = sys.executable.replace("\\", "/")
    if " " in py:  # a quoted program path is not portable between Git Bash and PowerShell
        py = "python" if shutil.which("python") else "py"
    return f'{py} "{(ENGINE_DIR / "scripts" / "sync.py").as_posix()}" --hook'


def install_hook(remove: bool = False) -> int:
    settings = load_json(SETTINGS_PATH, None)
    if settings is None and SETTINGS_PATH.exists():
        print(f"{SETTINGS_PATH} is not valid JSON, so it was left alone. Fix it, then try again.")
        return 1
    settings = settings or {}
    groups = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    kept = [g for g in groups if not any(HOOK_MARK in str(h.get("command", "")) and "--hook" in str(h.get("command", ""))
                                         for h in g.get("hooks", []))]
    if not remove:
        kept.append({"matcher": "startup", "hooks": [{"type": "command", "command": hook_command(), "timeout": 30}]})
    settings["hooks"]["SessionStart"] = kept
    if not kept:
        settings["hooks"].pop("SessionStart")
    if SETTINGS_PATH.exists():
        shutil.copy2(SETTINGS_PATH, SETTINGS_PATH.with_suffix(".json.bak"))
    save_json(SETTINGS_PATH, settings)
    if remove:
        print(f"Removed the star-triage hook from {SETTINGS_PATH}.")
    else:
        print(f"Added to {SETTINGS_PATH} (backup: settings.json.bak):\n  {hook_command()}")
        print("From now on, when Claude Code starts it pulls your approvals and installs them in the background.")
    return 0


def hook_installed() -> bool:
    if running_as_plugin():
        return True  # the plugin's own SessionStart hook
    settings = load_json(SETTINGS_PATH, {})
    return any(HOOK_MARK in str(h.get("command", "")) and "--hook" in str(h.get("command", ""))
               for g in settings.get("hooks", {}).get("SessionStart", []) for h in g.get("hooks", []))


def emit(user_msg: str, claude_msg: str = "") -> None:
    out = {"systemMessage": user_msg}
    if claude_msg:
        out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": claude_msg}
    print(json.dumps(out))


def lock_busy() -> bool:
    try:
        return time.time() - LOCK_PATH.stat().st_mtime < 30 * 60
    except OSError:
        return False


def run_hook() -> int:
    """Fast path for Claude Code's SessionStart hook. Never fails the session."""
    try:
        if running_as_plugin():
            write_link(engine=ENGINE_DIR)  # so `python star-triage.py` can find this version
            install_launcher()  # ~/.claude/star-triage.py: a setup command that survives updates
        if not LINKED or (not (ROOT / "installs.json").exists() and not (ROOT / "config.yml").exists()):
            return 0  # no data repo linked yet: stay silent
        messages = []
        update_line = self_update_message(engine_mismatch(ROOT))
        if update_line:
            messages.append(update_line)
        mode, wait_h, limit = settings()
        auto = mode != "manual"
        parts, extra, waiting = [], [], []  # auto modes: everything goes into one short line
        last = load_json(RESULT_PATH, {})
        if last and not last.get("shown"):
            if auto and "log" in last:
                for verb in ("installed", "updated", "removed"):
                    names = [str(r.get("name")) for r in last["log"] if r.get("action") == verb]
                    if names:
                        parts.append(f"{verb} " + ", ".join(names))
                if last.get("failed"):
                    parts.append(f"{len(last['failed'])} failed (see .star-triage/sync.log)")
                extra = last.get("skipped", []) + last.get("notes", [])
            else:
                bits = []
                if last.get("done"):
                    bits.append("installed/updated: " + "; ".join(d.split(": ", 1)[-1] for d in last["done"]))
                if last.get("failed"):
                    bits.append(f"{len(last['failed'])} failed (see .star-triage/sync.log)")
                bits += last.get("skipped", []) + last.get("notes", [])
                if bits:
                    messages.append("star-triage: " + " | ".join(bits))
            last["shown"] = True
            save_json(RESULT_PATH, last)
        stamp = load_json(HOOK_STAMP, {})
        if time.time() - stamp.get("at", 0) >= HOOK_EVERY_SECONDS and not lock_busy():
            save_json(HOOK_STAMP, {"at": time.time()})
            pull(quiet=True)
            items = load_json(INSTALLS_PATH, {}).get("items", {})
            steps = plan(items, load_json(STATE_PATH, {}), prune=False)
            if PAUSE_PATH.exists():
                if steps:
                    messages.append(f"star-triage is paused: {len(steps)} approved change(s) not applied. "
                                    "Resume: python star-triage.py resume")
            elif auto:
                run, waiting, capped, high = hold_back(steps, wait_h, limit, history_read())
                if run:
                    start_background()
                    names = [short_name(k, i) for a, k, i in run if a != "remove" and i.get("status") != "blocked"]
                    if names:
                        parts.append("installing " + ", ".join(names[:5]) + " now")
                if waiting:
                    parts.append(waiting_text(waiting, wait_h))
                if capped:
                    parts.append(names_and_more([short_name(k, i) for _, k, i in capped])
                                 + f" wait{'s' if len(capped) == 1 else ''} for tomorrow (daily limit {limit})")
                if high:
                    parts.append(f"{len(high)} High risk, never automatic (run: python star-triage.py sync)")
            elif steps:
                start_background()
                names = ", ".join(f"{a} {describe(i)}" for a, k, i in steps[:5])
                messages.append(f"star-triage: installing {len(steps)} approved item(s) in the background ({names}). "
                                "Skills work right away; plugins and MCP servers load next time you start Claude Code.")
        if parts or extra:
            line = " | ".join([f"star-triage {mode}: " + ("; ".join(parts) or "nothing installed")] + extra)
            messages.insert(0, line + (f" {NOW_HINT}" if auto and waiting else ""))
        if messages:
            emit("\n".join(messages), "\n".join(messages))
    except Exception as exc:  # a broken sync must never break Claude Code
        log(f"ERR hook: {exc}")
    return 0


def start_background(flag: str = "--background") -> None:
    cmd = [sys.executable, str(ENGINE_DIR / "scripts" / "sync.py"), flag]
    kwargs: dict = {"cwd": ROOT, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True,
                    "env": dict(os.environ, STAR_TRIAGE_DATA=str(ROOT))}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # detached, new group, no window
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


# ----------------------------------------------------------------- the plugin keeps itself up to date

def self_update_due(last: dict, now: float) -> bool:
    """At most one attempt a day."""
    try:
        tried = float(last.get("tried") or 0)
    except (TypeError, ValueError):
        tried = 0.0
    return now - tried >= SELF_UPDATE_EVERY


def self_update_allowed() -> bool:
    """Only the installed plugin updates itself, and only with auto_update on (the default)."""
    return (running_as_plugin() and config_on(read_simple_config(ROOT / "config.yml"), "auto_update")
            and find_claude() is not None)


def self_update_message(old: str | None, now: float | None = None, allowed: bool | None = None) -> str:
    """The start-up line about the plugin's own version. `old` is engine_mismatch()'s text (None
    when the plugin is not older than the reviews). Starts the background update when it is due."""
    now = time.time() if now is None else now
    last = load_json(SELF_UPDATE_PATH, {})
    if last.get("done") and not last.get("shown"):
        save_json(SELF_UPDATE_PATH, {**last, "shown": True})
        version = last.get("version") or "?"
        if not last.get("ok"):
            return f"star-triage could not update itself to {version} (see .star-triage/sync.log). {UPDATE_HOW}"
        if not old:
            return f"updated star-triage to {version}."
        if version == old.rsplit(" ", 1)[-1]:
            return f"updated star-triage to {version}; restart Claude Code to use it"
    if not old:
        return ""
    target = old.rsplit(" ", 1)[-1]
    allowed = self_update_allowed() if allowed is None else allowed
    if allowed and self_update_due(last, now):
        save_json(SELF_UPDATE_PATH, {"tried": now, "version": target, "done": False})
        start_background("--self-update")
        return f"updating star-triage to {target} in the background; restart Claude Code to use it"
    if allowed and not last.get("done") and last.get("version") == target:
        return f"updating star-triage to {target} in the background; restart Claude Code to use it"
    return f"{old}. {UPDATE_HOW}"


def self_update() -> int:
    """--self-update (started by the hook): the two update commands, never with --yes."""
    last = load_json(SELF_UPDATE_PATH, {})
    claude, run, ok = find_claude(), Runner(False, False), True
    for command in UPDATE_COMMANDS:  # "claude plugin marketplace update star-triage", "claude plugin update ..."
        ok = ok and bool(claude) and run([claude, *command.split()[1:]])
    save_json(SELF_UPDATE_PATH, {**last, "done": True, "ok": ok, "shown": False})
    log(f"{'ok ' if ok else 'ERR'} self-update to {last.get('version', '?')}")
    return 0


def marketplace_auto_update(on: bool = True) -> str:
    """Claude Code's own auto-update for the star-triage marketplace, through the documented
    `autoUpdate` field of its `extraKnownMarketplaces` entry in ~/.claude/settings.json (that
    entry is what `claude plugin marketplace add` writes). Only an existing entry is changed,
    with a backup; nothing else in the file is touched. Returns 'changed', 'already',
    'not-declared' or 'unreadable'."""
    settings = load_json(SETTINGS_PATH, None)
    if not isinstance(settings, dict):
        return "unreadable" if SETTINGS_PATH.exists() else "not-declared"
    entry = (settings.get("extraKnownMarketplaces") or {}).get(PLUGIN_MARKETPLACE)
    if not isinstance(entry, dict):
        return "not-declared"
    if bool(entry.get("autoUpdate")) == on:
        return "already"
    entry["autoUpdate"] = on
    shutil.copy2(SETTINGS_PATH, SETTINGS_PATH.with_suffix(".json.bak"))
    save_json(SETTINGS_PATH, settings)
    return "changed"


# ----------------------------------------------------------------- pause, history, undo

def pause(on: bool) -> int:
    if on:
        STATE_DIR.mkdir(exist_ok=True)
        PAUSE_PATH.write_text(dt.datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
        print("Paused. Nothing is installed or removed automatically on this computer until you run: "
              "python star-triage.py resume")
        print("(`python star-triage.py sync` by hand still works. Reviews on GitHub are not affected.)")
    else:
        was = PAUSE_PATH.exists()
        PAUSE_PATH.unlink(missing_ok=True)
        print("Resumed. Automatic installs run again the next time Claude Code starts." if was
              else "Not paused: automatic installs are already running.")
    return 0


def show_history() -> int:
    rows = history_read()[-20:][::-1]
    if not rows:
        print("No automatic installs or removals yet.")
        return 0
    for row in rows:
        when = str(row.get("at", ""))[:16].replace("T", " ")
        print(f"{when}  {row.get('what') or row['key']:40} {row.get('why', row.get('action', ''))}")
    return 0


def undo(scope: str) -> int:
    """Remove everything the most recent automatic batch installed, and untick those notes."""
    rows = history_read()
    batches = [r.get("batch") for r in rows if r.get("auto") and r.get("action") == "installed"]
    if not batches:
        print("Nothing to undo: nothing has been installed automatically yet.")
        return 0
    batch = batches[-1]
    state = load_json(STATE_PATH, {})
    items = load_json(INSTALLS_PATH, {}).get("items", {})
    keys = list(dict.fromkeys(r["key"] for r in rows if r.get("batch") == batch and r.get("auto")
                              and r.get("action") == "installed" and r["key"] in state))
    when = str(batch)[:16].replace("T", " ")
    if not keys:
        print(f"The most recent automatic batch ({when}) is already removed.")
        return 0
    steps = [("remove", key, items.get(key) or {"repo": key, "kind": state[key]["kind"], "name": state[key].get("name", ""),
                                                 "spec": state[key].get("spec", {}), "note": ""}) for key in keys]
    print(f"The most recent automatic batch ({when}) installed:")
    for _, key, item in steps:
        print(f"  {key}: {describe(item)}")
    try:
        answer = input(f"Remove {'it' if len(steps) == 1 else f'these {len(steps)}'} and untick "
                       f"{'its note' if len(steps) == 1 else 'their notes'}? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if not answer.startswith("y"):
        print("Nothing was changed.")
        return 0
    res = execute(steps, state, Sync(Runner(False, True), find_claude(), scope), True)
    now = dt.datetime.now().isoformat(timespec="seconds")
    for row in res["log"]:
        note = (items.get(row["key"]) or {}).get("note")
        if note:
            set_note_approve(note, False)
    history_add([{"at": now, "batch": batch, "auto": False, **row, "why": "removed: undone by you"}
                 for row in res["log"]])
    print(f"\nRemoved {len(res['log'])} of {len(steps)}. Their notes are unticked, so they stay removed.")
    return 1 if res["failed"] else 0


# ----------------------------------------------------------------- --now

def install_now(dry: bool, no_pull: bool, scope: str, out: dict | None = None) -> int:
    """`sync --now`: what the background run would install, now. Only the safety delay is
    skipped: the daily limit, pause, and the High-risk and blocked rules stay, nothing asks a
    question (a blocked item still needs `sync` in your own terminal and a typed "install"),
    and every step goes to the history, so `undo` covers it. `out`, when given, receives what
    happened (now.py builds its summary from it)."""
    out = {} if out is None else out
    if PAUSE_PATH.exists():
        print("star-triage is paused on this computer, so nothing is installed. Resume first: "
              "python star-triage.py resume")
        out["paused"] = True
        return 0
    if lock_busy():
        print("A background sync is running right now. Try again in a few minutes.")
        out["busy"] = True
        return 1
    if not dry:
        STATE_DIR.mkdir(exist_ok=True)
        LOCK_PATH.write_text(str(os.getpid()))
    try:
        if not no_pull and not dry:
            print("Pulling your latest approvals...")
            pull(quiet=False)
        items = load_json(INSTALLS_PATH, {}).get("items", {})
        state = load_json(STATE_PATH, {})
        mode, _, limit = settings()
        steps, capped, high = plan(items, state, prune=False), [], []
        if mode != "manual":
            steps, _, capped, high = hold_back(steps, 0, limit, history_read())
        result = {"done": [], "failed": [], "skipped": [], "notes": [], "log": [], "at": ""}
        if steps:
            sync = Sync(Runner(dry, interactive=False), find_claude(), scope)
            result = execute(steps, state, sync, confirm_blocked=False)
        if not dry and result["log"]:
            at = dt.datetime.now().isoformat(timespec="seconds")
            history_add([{"at": at, "batch": result["at"], "auto": True, "now": True, "mode": mode, **row}
                         for row in result["log"]])
        out.update(result=result, capped=capped, high=high, mode=mode, limit=limit)
        if not steps and not capped and not high:
            print("Nothing waits: everything you approved is installed.")
        for _, key, item in capped:
            print(f"\n{short_name(key, item)} waits for tomorrow (daily limit {limit}).")
        for _, key, _ in high:
            print(f"\n{key} is High risk, so it is never installed automatically. To install it, run in your "
                  "own terminal, in your data repo: python star-triage.py sync")
        for line in result["skipped"] + result["notes"]:
            print(f"\nNote: {line}")
        if dry:
            print("\nDry run: nothing was changed.")
        elif result["failed"]:
            print(f"\n{len(result['failed'])} step(s) failed. Details: {LOG_PATH.relative_to(ROOT)}. See {GUIDE}")
        elif result["done"]:
            print("\nDone." + (" Restart Claude Code to load new plugins and MCP servers." if needs_restart(result) else ""))
        return 1 if result["failed"] else 0
    finally:
        if not dry:
            LOCK_PATH.unlink(missing_ok=True)


# ----------------------------------------------------------------- main

def main() -> int:
    safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)  # AUTOMATIC above reads the exact flags
    ap.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    ap.add_argument("--now", action="store_true", help="install what is approved now, without waiting for the "
                    "safety delay (the daily limit, High-risk and blocked rules still apply)")
    ap.add_argument("--no-pull", action="store_true", help="skip `git pull` first")
    ap.add_argument("--status", action="store_true", help="list reviewed installs and their state here")
    ap.add_argument("--uninstall", metavar="NAME", help="remove one item and untick its approve box")
    ap.add_argument("--prune", action="store_true", help="also remove items that are no longer in installs.json")
    ap.add_argument("--scope", choices=["user", "project", "local"], help="plugin scope (default: install_scope)")
    ap.add_argument("--install-hook", action="store_true", help="sync automatically when Claude Code starts")
    ap.add_argument("--remove-hook", action="store_true", help="stop syncing automatically")
    ap.add_argument("--pause", action="store_true", help="stop everything automatic on this computer")
    ap.add_argument("--resume", action="store_true", help="let automatic installs run again")
    ap.add_argument("--undo", action="store_true", help="remove the most recent automatic batch")
    ap.add_argument("--history", action="store_true", help="the last 20 automatic installs and removals")
    ap.add_argument("--hook", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--self-update", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.hook:
        return run_hook()
    if args.self_update:
        return self_update() if LINKED else 0
    if args.background and not LINKED:
        return 0  # no linked data repo: nothing to do, and never the current folder
    if args.install_hook or args.remove_hook:
        if args.install_hook and running_as_plugin():
            print("Not needed: the star-triage plugin already syncs automatically when Claude Code starts.")
            return 0
        return install_hook(remove=args.remove_hook)
    if not (ROOT / "config.yml").exists():
        print("No star-triage data repo found here. Run this from your data repo (the one with config.yml "
              "and tracker/), or set one up with: " + launcher_command("setup"))
        return 1

    if args.pause or args.resume:
        return pause(args.pause)
    if args.history:
        return show_history()
    if args.undo:
        return undo(args.scope or read_scope())
    if args.now:
        return install_now(args.dry_run, args.no_pull, args.scope or read_scope())

    background = args.background
    if background:
        if PAUSE_PATH.exists() or lock_busy():
            return 0
        STATE_DIR.mkdir(exist_ok=True)
        LOCK_PATH.write_text(str(os.getpid()))
    try:
        if not args.no_pull and not args.dry_run and not background and not args.status:
            print("Pulling your latest approvals...")
            pull(quiet=False)
        items = load_json(INSTALLS_PATH, {}).get("items", {})
        state = load_json(STATE_PATH, {})

        if args.status:
            approved = wanted(items)
            print(f"{len(items)} reviewed item(s) that can be installed:")
            for key, item in sorted(items.items()):
                here = "installed" if key in state else "not installed"
                if key in approved:
                    flag = "approved"
                elif item.get("approved") and note_approve(item.get("note", "")) is False:
                    flag = "unticked"
                else:
                    flag = {"queued": "approved", "available": "available"}.get(item.get("status"), item.get("status", ""))
                print(f"  {key:40} {describe(item):38} {flag:10} {here}")
            print(f"\nAutomatic sync when Claude Code starts: {'on' if hook_installed() else 'off'}"
                  + ("" if hook_installed() else "  (turn on: python star-triage.py sync --install-hook)"))
            mode, wait_h, limit = settings()
            print(f"star-triage mode: {mode} ({MODE_SHORT[mode]})"
                  + (f", after a {wait_h} h safety delay, at most {limit} a day" if mode != "manual" else ""))
            if PAUSE_PATH.exists():
                print("PAUSED: nothing automatic happens on this computer. Resume: python star-triage.py resume")
            return 0

        if args.uninstall:
            want = args.uninstall.lower()
            key = next((k for k, it in items.items() if want in (k.lower(), str(it.get("name", "")).lower())), None)
            key = key or next((k for k, o in state.items() if want in (k.lower(), str(o.get("name", "")).lower())), None)
            if not key or key not in state:
                print(f"'{args.uninstall}' is not installed by star-triage. Try: python star-triage.py sync --status")
                return 1
            item = items.get(key) or {"repo": key, "kind": state[key]["kind"], "name": state[key].get("name", ""),
                                      "spec": state[key].get("spec", {}), "note": ""}
            sync = Sync(Runner(args.dry_run, True), find_claude(), args.scope or read_scope())
            res = execute([("remove", key, item)], state, sync, True)
            if item.get("note") and not args.dry_run:
                set_note_approve(item["note"], False)
                print(f"Unticked approve on {item['note']}, so it stays removed.")
            return 1 if res["failed"] else 0

        steps = plan(items, state, args.prune)
        mode, wait_h, limit = settings()
        high = []
        if background and mode != "manual":
            steps, _, _, high = hold_back(steps, wait_h, limit, history_read())
        if not steps:
            if not background:
                if wanted(items):
                    print("Everything you approved is installed.")
                else:
                    waiting = [(k, it) for k, it in sorted(items.items())
                               if it.get("kind") in ("plugin", "skill", "mcp", "cli")
                               and it.get("status") in ("available", "queued")]
                    if waiting:
                        # Say why each one waits, instead of asking for a merge already done (UT-14).
                        print("Nothing is approved to install yet. These wait for you:")
                        obsidian = config_on(read_simple_config(ROOT / "config.yml"), "obsidian")
                        for key, it in waiting:
                            why = ("approve is unticked on its note" if it.get("status") == "queued"
                                   else f"verdict {it.get('verdict') or '?'}, risk {it.get('risk') or '?'}")
                            how = (f"tick `approve` in {it.get('note') or 'its note'}" if obsidian
                                   else f"run: python star-triage.py approve {key}")
                            print(f"  {key}: {why}. To install it, {how}.")
                    else:
                        print("Nothing to install yet. Reviews that find something installable show up here.")
                if not hook_installed():
                    print("Tip: run `python star-triage.py sync --install-hook` to have this happen automatically.")
            return 0

        claude = find_claude()
        if not claude and any(i["kind"] in ("plugin", "mcp") for _, _, i in steps) and not background:
            print(f"Claude Code CLI not found: plugins and MCP servers will be skipped. See {GUIDE}")
        sync = Sync(Runner(args.dry_run, interactive=not background), claude, args.scope or read_scope())
        result = execute(steps, state, sync, confirm_blocked=not background and not args.dry_run)

        if background:
            now = dt.datetime.now().isoformat(timespec="seconds")
            history_add([{"at": now, "batch": result["at"], "auto": True, "mode": mode, **row}
                         for row in result["log"]])
            result["skipped"] += [f"{key} is High risk, so it is never installed automatically; "
                                  "run `python star-triage.py sync` yourself to install it" for _, key, _ in high]
            save_json(RESULT_PATH, {**result, "mode": mode, "shown": False})
            return 0
        for line in result["skipped"] + result["notes"]:
            print(f"\nNote: {line}")
        if args.dry_run:
            print("\nDry run: nothing was changed.")
        elif result["failed"]:
            print(f"\n{len(result['failed'])} step(s) failed. Details: {LOG_PATH.relative_to(ROOT)}. See {GUIDE}")
        else:
            print("\nDone." + (" Restart Claude Code to load new plugins and MCP servers." if needs_restart(result) else ""))
        if not hook_installed() and not args.dry_run:
            print("Tip: run `python star-triage.py sync --install-hook` to have this happen automatically.")
        return 1 if result["failed"] else 0
    finally:
        if background:
            LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
