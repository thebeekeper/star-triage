"""Install what you approved on this computer: plugins, skills, MCP servers and CLI tools.

    python star-triage.py sync                 pull, then install, update or remove to match your approvals
    python star-triage.py sync --dry-run       show the plan, change nothing
    python star-triage.py sync --status        everything reviewed that can be installed, and its state here
    python star-triage.py sync --uninstall X   remove one (owner/name or its name) and untick its approve box

Automatic mode: the star-triage Claude Code plugin runs this in the background whenever
Claude Code starts (its SessionStart hook calls `sync.py --hook`). Without the plugin,
`--install-hook` adds the same hook to your settings.json, and `--remove-hook` takes it out.

What counts as approved
  - Items the workflow approved (verdict Install, not High risk, nothing blocking), once you
    merged the PR. They arrive in installs.json, pinned to an exact commit or version.
  - Anything you tick `approve` on in Obsidian. Untick it and the item is removed again.
  - Blocked items (high-severity scan findings, binaries) are never installed in the
    background. Run this script by hand to confirm one.

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
from doctor import find_claude  # noqa: E402  (standard library only)

from paths import (ENGINE_DIR, TROUBLESHOOTING_URL, find_data_root, linked_data_root,  # noqa: E402
                   running_as_plugin, safe_console, write_link)

# Automatic runs use only the data repo you linked (setup, or STAR_TRIAGE_DATA), never the
# folder Claude Code happens to be opened in: that folder may be someone else's project.
AUTOMATIC = bool({"--hook", "--background"} & set(sys.argv[1:]))
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


def describe(item: dict) -> str:
    spec = item.get("spec", {})
    return {"plugin": f"plugin {spec.get('plugin', '')}",
            "skill": "skill " + ", ".join(s["name"] for s in spec.get("skills", [])[:4]),
            "mcp": f"MCP server {item.get('name', '')}",
            "cli": f"{spec.get('package', '')} {spec.get('version', '')}"}.get(item["kind"], item["kind"])


def execute(steps, state: dict, sync: Sync, confirm_blocked: bool) -> dict:
    result = {"at": dt.datetime.now().isoformat(timespec="seconds"), "done": [], "failed": [], "skipped": []}
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
        out = handler(item, action, state.get(key))
        if out is None:
            result["failed"].append(label)
            continue
        result["done"].append(label)
        if sync.run.dry:
            continue
        if action == "remove":
            state.pop(key, None)
        else:
            state[key] = {"kind": item["kind"], "name": item.get("name", ""), "fp": fingerprint(item),
                          "spec": item.get("spec", {}), "at": dt.date.today().isoformat(), **out}
        save_json(STATE_PATH, state)
    result["notes"] = sync.notes
    return result


def pull(quiet: bool) -> None:
    if not (ROOT / ".git").exists():
        return
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(["git", "pull", "--ff-only", "--autostash", "-q"], cwd=ROOT, env=env,
                             capture_output=quiet, timeout=30, stdin=subprocess.DEVNULL)
        if res.returncode:
            log("git pull failed; using the approvals already on this computer")
    except (OSError, subprocess.TimeoutExpired):
        log("git pull timed out; using the approvals already on this computer")


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
        if not LINKED or (not (ROOT / "installs.json").exists() and not (ROOT / "config.yml").exists()):
            return 0  # no data repo linked yet: stay silent
        messages = []
        last = load_json(RESULT_PATH, {})
        if last and not last.get("shown"):
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
            if steps:
                start_background()
                names = ", ".join(f"{a} {describe(i)}" for a, k, i in steps[:5])
                messages.append(f"star-triage: installing {len(steps)} approved item(s) in the background ({names}). "
                                "Skills work right away; plugins and MCP servers load next time you start Claude Code.")
        if messages:
            emit("\n".join(messages), "\n".join(messages))
    except Exception as exc:  # a broken sync must never break Claude Code
        log(f"ERR hook: {exc}")
    return 0


def start_background() -> None:
    cmd = [sys.executable, str(ENGINE_DIR / "scripts" / "sync.py"), "--background"]
    kwargs: dict = {"cwd": ROOT, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True,
                    "env": dict(os.environ, STAR_TRIAGE_DATA=str(ROOT))}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # detached, new group, no window
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


# ----------------------------------------------------------------- main

def main() -> int:
    safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)  # AUTOMATIC above reads the exact flags
    ap.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    ap.add_argument("--no-pull", action="store_true", help="skip `git pull` first")
    ap.add_argument("--status", action="store_true", help="list reviewed installs and their state here")
    ap.add_argument("--uninstall", metavar="NAME", help="remove one item and untick its approve box")
    ap.add_argument("--prune", action="store_true", help="also remove items that are no longer in installs.json")
    ap.add_argument("--scope", choices=["user", "project", "local"], help="plugin scope (default: install_scope)")
    ap.add_argument("--install-hook", action="store_true", help="sync automatically when Claude Code starts")
    ap.add_argument("--remove-hook", action="store_true", help="stop syncing automatically")
    ap.add_argument("--hook", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.hook:
        return run_hook()
    if args.background and not LINKED:
        return 0  # no linked data repo: nothing to do, and never the current folder
    if args.install_hook or args.remove_hook:
        if args.install_hook and running_as_plugin():
            print("Not needed: the star-triage plugin already syncs automatically when Claude Code starts.")
            return 0
        return install_hook(remove=args.remove_hook)
    if not (ROOT / "config.yml").exists():
        print("No star-triage data repo found here. Run this from your data repo (the one with config.yml "
              "and tracker/), or link one with: python <engine>/scripts/setup.py")
        return 1

    background = args.background
    if background:
        if lock_busy():
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
        if not steps:
            if not background:
                print("Everything you approved is installed." if wanted(items) else
                      "Nothing approved yet. Approve installs by merging a review PR, or tick `approve` on a note.")
                if not hook_installed():
                    print("Tip: run `python star-triage.py sync --install-hook` to have this happen automatically.")
            return 0

        claude = find_claude()
        if not claude and any(i["kind"] in ("plugin", "mcp") for _, _, i in steps) and not background:
            print(f"Claude Code CLI not found: plugins and MCP servers will be skipped. See {GUIDE}")
        sync = Sync(Runner(args.dry_run, interactive=not background), claude, args.scope or read_scope())
        result = execute(steps, state, sync, confirm_blocked=not background and not args.dry_run)

        if background:
            save_json(RESULT_PATH, {**result, "shown": False})
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
