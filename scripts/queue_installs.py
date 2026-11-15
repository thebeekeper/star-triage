"""Work out exactly how each reviewed repo would be installed, and queue the approved ones.

Runs after the notes are validated. Claude only *proposes* an install method
(.triage/proposals/<note>.json). Everything below is decided by this script, from the
trusted scan copy and from the package registries, never from text Claude wrote:

  plugin  the repo is a Claude Code plugin -> pinned to the reviewed commit, added to
          .claude-plugin/marketplace.json
  skill   SKILL.md folders -> copied from the reviewed commit into ~/.claude/skills
  mcp     an MCP server: a hosted https URL that appears in the repo itself, or an
          npm / PyPI package whose registry entry links back to this exact repo,
          pinned to its current version
  cli     a command-line tool from npm / PyPI, same checks and pinning
  none    nothing to install (a library, an app, a pattern...)

An item is APPROVED (installed automatically once the PR is merged) when Claude's
verdict is Install, the risk is not High, and nothing blocks it. Anything else that
can be installed is AVAILABLE: tick `approve` on its note in Obsidian to install it.
BLOCKED items (high-severity scan findings, binaries, unknown commit) are never
installed automatically.

Writes installs.json, .claude-plugin/marketplace.json, the note fields install_kind /
auto_install / approve, an "Install plan (automated)" note section, and
.triage/install-summary.md for the PR description.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import ROOT, TRUSTED_DIR, WORK_DIR, github_user, load_config, normalize_url, split_note, write_note

INSTALLS_PATH = ROOT / "installs.json"
MARKET_PATH = ROOT / ".claude-plugin" / "marketplace.json"
PLAN_RE = re.compile(r"\n## Install plan \(automated\)\n.*?(?=\n## |\Z)", re.S)

NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
PYPI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SERVER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SAFE_ARG = re.compile(r"^[A-Za-z0-9@%+=:,./_-]{1,100}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# A command name: no paths, no extensions (so not /bin/sh, bash.exe or python3.12).
COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# PyPI does not list a package's commands, so a proposed `command` is also refused if it is a
# shell, interpreter or downloader. Setup steps use an allow-list instead (see check_setup).
NOT_A_COMMAND = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh", "iex", "irm", "curl", "wget",
                       "sudo", "su", "rm", "del", "python", "python3", "py", "node", "deno", "bun", "npx", "npm",
                       "pnpm", "yarn", "pip", "pip3", "pipx", "uv", "uvx", "git", "eval", "exec", "source", "."}
GITHUB_REPO = re.compile(r"github\.com[/:]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?(?:[/#?].*)?$", re.I)


# ----------------------------------------------------------------- network

def fetch_json(url: str, token: str | None = None) -> dict | None:
    headers = {"User-Agent": "star-triage", "Accept": "application/json"}
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def github_id(full_name: str) -> int | None:
    data = fetch_json(f"https://api.github.com/repos/{full_name}",
                      os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    return data.get("id") if isinstance(data, dict) else None


def same_repo(linked: str | None, item: dict) -> bool:
    """True if the registry's repo link is this repo (renames and transfers allowed)."""
    if not linked:
        return False
    if linked.lower() == item["repo"].lower():
        return True
    want = item.get("id") or github_id(item["repo"])
    return bool(want) and github_id(linked) == want


def repo_from_url(url) -> str | None:
    if isinstance(url, dict):
        url = url.get("url")
    m = GITHUB_REPO.search(str(url or ""))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def npm_package(name: str) -> dict | None:
    data = fetch_json("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@"))
    if not isinstance(data, dict):
        return None
    version = (data.get("dist-tags") or {}).get("latest")
    meta = (data.get("versions") or {}).get(version) or {}
    if not version or meta.get("deprecated"):
        return None
    # The commands this version installs: the keys of `bin`, or the package's own name
    # (without the scope) when `bin` is a single path.
    bin_field = meta.get("bin")
    bins = list(bin_field) if isinstance(bin_field, dict) else [name.rsplit("/", 1)[-1]] if bin_field else []
    return {"version": version, "repo": repo_from_url(meta.get("repository") or data.get("repository")),
            "has_bin": bool(bins), "bins": bins}


def pypi_package(name: str) -> dict | None:
    data = fetch_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    info = (data or {}).get("info") if isinstance(data, dict) else None
    if not info or not info.get("version"):
        return None
    urls = list((info.get("project_urls") or {}).values()) + [info.get("home_page"), info.get("download_url")]
    repos = [r for r in (repo_from_url(u) for u in urls) if r]
    # Prefer the link that is the bare repository (issue/doc links often point elsewhere).
    return {"version": info["version"], "repo": repos[0] if repos else None, "all_repos": repos, "has_bin": True,
            "extras": [str(e).lower() for e in info.get("provides_extra") or []]}


# ----------------------------------------------------------------- deciding

def load_proposal(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def check_url(url: str, scan: dict) -> str | None:
    """Return an error, or None if this hosted MCP URL is acceptable."""
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return "invalid URL"
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return "the URL must be plain https"
    try:
        ipaddress.ip_address(host)
        return "the URL must use a domain name, not an IP address"
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        return "the URL points at a local address"
    seen = {u.rstrip("/") for u in scan.get("urls", [])}
    if url.split("#")[0].split("?")[0].rstrip("/") not in seen:
        return "that URL does not appear anywhere in the repo"
    return None


def check_setup(setup, scan: dict, commands) -> str | None:
    """A post-install command must be the tool's own command, exactly as the README shows it.

    `commands` is the allow-list: the command names the package itself installs."""
    if not isinstance(setup, list) or not 1 <= len(setup) <= 6 or \
            not all(isinstance(a, str) and SAFE_ARG.match(a) for a in setup):
        return "it must be a short list of plain words"
    if not COMMAND_NAME.match(setup[0]) or setup[0] not in set(commands):
        return f"'{setup[0]}' is not the tool's own command"
    wanted = " ".join(setup)
    for line in scan.get("readme_commands", []):
        if line == wanted or line.startswith(wanted + " "):
            return None
    return "that command does not appear in the README"


def resolve(item: dict, scan: dict, proposal: dict) -> dict:
    """Decide the install for one repo. Returns {kind, name, spec, problem}."""
    plugin = (scan.get("plugin") or {}).get("name")
    plugin_at_root = bool(plugin) and "not a Claude Code plugin at the repository root" not in \
        (scan.get("auto_install") or {}).get("reasons", [])
    kind = str(proposal.get("kind") or "").lower()

    # The scan, not the proposal, decides whether something is a plugin. A plugin is the
    # most complete way to install a repo (it brings its own skills and MCP servers).
    if plugin_at_root and kind != "none":
        return {"kind": "plugin", "name": plugin, "spec": {"plugin": plugin}}
    if kind == "plugin" and not plugin_at_root:
        kind = "skill" if scan.get("skill_list") else ""
        if not kind:
            return {"kind": "none", "name": "", "spec": {}, "problem": "not a Claude Code plugin at the repository root"}
    if kind == "skill" or (not kind and scan.get("skill_list")):
        skills = scan.get("skill_list") or []
        if not skills:
            return {"kind": "none", "name": "", "spec": {}, "problem": "no SKILL.md folders found"}
        return {"kind": "skill", "name": skills[0]["name"], "spec": {"skills": skills}}

    if kind in ("mcp", "cli"):
        name = str(proposal.get("name") or "").lower().strip()
        if kind == "mcp" and proposal.get("url"):
            if not SERVER_NAME.match(name):
                return {"kind": "manual", "name": "", "spec": {}, "problem": "invalid server name"}
            url = str(proposal["url"]).strip()
            err = check_url(url, scan)
            if err:
                return {"kind": "manual", "name": name, "spec": {}, "problem": f"hosted MCP URL rejected: {err}"}
            return {"kind": "mcp", "name": name, "spec": {"transport": "http", "url": url}}

        registry, package = str(proposal.get("registry") or "").lower(), str(proposal.get("package") or "").strip()
        if registry not in ("npm", "pypi") or not (NPM_NAME if registry == "npm" else PYPI_NAME).match(package):
            return {"kind": "manual", "name": name, "spec": {}, "problem": "no valid npm or PyPI package"}
        info = npm_package(package) if registry == "npm" else pypi_package(package)
        if not info:
            return {"kind": "manual", "name": name, "spec": {},
                    "problem": f"{registry} package '{package}' not found (or deprecated)"}
        linked = [info["repo"]] + [r for r in info.get("all_repos", []) if r != info["repo"]]
        if not any(same_repo(r, item) for r in linked if r):
            return {"kind": "manual", "name": name, "spec": {},
                    "problem": f"{registry} package '{package}' does not link back to {item['repo']}"}
        if registry == "npm" and not info["has_bin"]:
            return {"kind": "manual", "name": name, "spec": {}, "problem": f"'{package}' has no command to run"}
        spec = {"registry": registry, "package": package, "version": info["version"]}
        if registry == "pypi":
            extras = [str(e).lower() for e in proposal.get("extras") or []]
            unknown = [e for e in extras if e not in info.get("extras", [])]
            if unknown or len(extras) > 5:
                return {"kind": "manual", "name": name, "spec": {},
                        "problem": f"'{package}' has no extra(s) {', '.join(unknown) or '(too many)'}"}
            command = str(proposal.get("command") or "").strip()
            if command and (not COMMAND_NAME.match(command) or command.lower() in NOT_A_COMMAND):
                return {"kind": "manual", "name": name, "spec": {}, "problem": "invalid command name"}
            if extras:
                spec["extras"] = extras
            if command:
                spec["command"] = command
        if kind == "cli":
            setup = proposal.get("setup") or []
            if setup:
                own = info["bins"] if registry == "npm" else [package, spec.get("command", "")]
                err = check_setup(setup, scan, own)
                if err:
                    return {"kind": "manual", "name": name, "spec": {}, "problem": f"setup step rejected: {err}"}
                spec["setup"] = setup
            return {"kind": "cli", "name": package.rsplit("/", 1)[-1], "spec": spec}
        if not SERVER_NAME.match(name):
            name = re.sub(r"[^a-z0-9_-]", "-", package.rsplit("/", 1)[-1].lower())[:40].strip("-") or "server"
        args = proposal.get("args") or []
        if not isinstance(args, list) or len(args) > 10 or not all(isinstance(a, str) and SAFE_ARG.match(a) for a in args):
            return {"kind": "manual", "name": name, "spec": {}, "problem": "unsafe or invalid server arguments"}
        env = []
        for e in proposal.get("env") or []:
            if isinstance(e, dict) and ENV_NAME.match(str(e.get("name", ""))):
                env.append({"name": e["name"], "required": bool(e.get("required"))})
        spec.update({"transport": "stdio", "args": args, "env": env})
        return {"kind": "mcp", "name": name, "spec": spec}

    why = str(proposal.get("why") or "nothing to install")[:80]
    return {"kind": "none", "name": "", "spec": {}, "problem": why}


def pypi_requirement(spec: dict) -> str:
    extras = f"[{','.join(spec['extras'])}]" if spec.get("extras") else ""
    return f"{spec['package']}{extras}=={spec['version']}"


def launch_command(spec: dict) -> list[str]:
    """How a stdio MCP server is started. Kept identical to sync.py."""
    if spec["registry"] == "npm":
        return ["npx", "-y", f"{spec['package']}@{spec['version']}", *spec.get("args", [])]
    command = spec.get("command") or spec["package"]
    return ["uvx", "--from", pypi_requirement(spec), command, *spec.get("args", [])]


def describe(entry: dict) -> str:
    kind, spec = entry["kind"], entry.get("spec", {})
    if kind == "plugin":
        return f"Claude Code plugin `{spec['plugin']}`, pinned to commit `{entry['commit'][:12]}`"
    if kind == "skill":
        names = ", ".join(f"`{s['name']}`" for s in spec["skills"][:6])
        more = f" and {len(spec['skills']) - 6} more" if len(spec["skills"]) > 6 else ""
        return f"skill(s) {names}{more} into ~/.claude/skills, from commit `{entry['commit'][:12]}`"
    if kind == "mcp" and spec.get("transport") == "http":
        return f"hosted MCP server `{entry['name']}` at {spec['url']}"
    if kind == "mcp":
        return f"MCP server `{entry['name']}` run with `{' '.join(launch_command(spec))}`"
    if kind == "cli":
        setup = f", then runs `{' '.join(spec['setup'])}`" if spec.get("setup") else ""
        what = pypi_requirement(spec) if spec["registry"] == "pypi" else f"{spec['package']}@{spec['version']}"
        return f"command-line tool {what} from {spec['registry']}{setup}"
    return ""


def plan_section(entry: dict) -> str:
    status = entry["status"]
    lines = ["## Install plan (automated)", ""]
    if status == "none":
        lines.append(f"Nothing to install automatically ({entry.get('problem') or 'not a tool'}).")
    elif status == "manual":
        lines.append(f"Not installed automatically: {entry['problem']}. Use the Install section below by hand.")
    else:
        lines.append(f"Installs: {describe(entry)}.")
        if status == "queued":
            lines.append("Status: **approved**. It installs by itself on your computer once this is merged "
                         "(untick `approve` in Obsidian to stop it).")
        elif status == "available":
            lines.append("Status: available, not approved. Tick `approve` on this note in Obsidian to install it.")
        else:
            lines.append(f"Status: **blocked** ({entry['blocked']}). Never installed automatically.")
        for e in entry.get("spec", {}).get("env", []) if entry["kind"] == "mcp" else []:
            need = "needs" if e["required"] else "can use"
            lines.append(f"This server {need} the environment variable `{e['name']}`.")
    return "\n".join(lines) + "\n"


def insert_plan(body: str, section: str) -> str:
    body = PLAN_RE.sub("", body)
    idx = body.find("\n## Install\n")
    if idx == -1:
        return body.rstrip("\n") + "\n\n" + section
    return body[:idx].rstrip("\n") + "\n\n" + section + body[idx:]


# ----------------------------------------------------------------- main

def load_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def main() -> None:
    cfg = load_config()
    batch = json.loads((TRUSTED_DIR / "new.json").read_text(encoding="utf-8"))
    installs = load_json_file(INSTALLS_PATH, {})
    installs.setdefault("about", "Installs reviewed by star-triage. Written by the workflow; "
                                 "approve or stop items with the approve checkbox on each note.")
    items = installs.setdefault("items", {})
    market = load_json_file(MARKET_PATH, None) or {
        "name": cfg.get("marketplace_name") or "my-stars",
        "owner": {"name": github_user(cfg)},
        "metadata": {"description": "Plugins reviewed by star-triage, pinned to reviewed commits"},
        "plugins": [],
    }
    plugins = {p.get("name"): p for p in market.get("plugins", [])}
    report = {"queued": [], "available": [], "blocked": [], "manual": []}

    for item in batch:
        note = ROOT / item["note_path"]
        if not note.exists():
            continue
        stem = note.stem
        scan = load_json_file(TRUSTED_DIR / "scan" / f"{stem}.json", {})
        proposal = load_proposal(WORK_DIR / "proposals" / f"{stem}.json")
        fm, body = split_note(note)
        fm.pop("approve", None)  # whatever the note says, Claude does not approve installs
        decision = resolve(item, scan, proposal)
        commit = scan.get("commit") or ""

        entry = {"repo": item["repo"], "url": item["url"], "note": item["note_path"],
                 "kind": decision["kind"], "name": decision["name"], "spec": decision["spec"],
                 "commit": commit, "verdict": str(fm.get("verdict", "")), "risk": str(fm.get("risk", "")),
                 "reviewed": str(fm.get("reviewed", ""))}
        blockers = list(scan.get("install_blockers") or ([] if scan else ["no security scan"]))
        if decision["kind"] in ("plugin", "skill") and not re.fullmatch(r"[0-9a-f]{40}", commit):
            blockers.append("reviewed commit unknown")
        if decision["kind"] == "plugin":
            clash = plugins.get(decision["name"])
            if clash and normalize_url(clash.get("homepage", "")) != normalize_url(item["url"]):
                blockers.append(f"another repo already uses the plugin name '{decision['name']}'")

        if decision["kind"] in ("none", "manual"):
            entry["status"], entry["problem"] = decision["kind"], decision.get("problem", "")
        elif blockers:
            entry["status"], entry["blocked"] = "blocked", "; ".join(dict.fromkeys(blockers))
        elif entry["verdict"] == "Install" and entry["risk"] != "High":
            entry["status"] = "queued"
        else:
            entry["status"] = "available"
        entry["approved"] = entry["status"] == "queued"

        # One entry per note: drop any older key for the same note (repo renamed or moved).
        for key in [k for k, v in items.items() if v.get("note") == item["note_path"] and k != item["repo"]]:
            items.pop(key)
        if entry["kind"] in ("none", "manual"):
            items.pop(item["repo"], None)
        else:
            items[item["repo"]] = entry

        entry["summary"] = str(fm.get("summary", ""))[:200]

        fm["install_kind"] = entry["kind"]
        fm["auto_install"] = entry["status"]
        # A re-review keeps the owner's own tick, read from the trusted new.json (select_new.py
        # copied it from the note before Claude ran). Otherwise the decision above stands.
        previous = (item.get("previous") or {}).get("approve") if item.get("retriage") else None
        fm["approve"] = previous if isinstance(previous, bool) else entry["approved"]
        write_note(note, fm, insert_plan(body, plan_section(entry)))
        if entry["status"] in report:
            report[entry["status"]].append(entry)
        print(f"{item['repo']}: {entry['kind']} -> {entry['status']}"
              + (f" ({entry.get('blocked') or entry.get('problem')})" if entry.get("blocked") or entry.get("problem") else ""))

    INSTALLS_PATH.write_text(json.dumps(installs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The marketplace lists every reviewed plugin, blocked ones included: it is only a
    # catalogue. What actually gets installed is decided by approvals in sync.py, which
    # never installs a blocked plugin without you typing "install".
    plugins = {}
    for entry in items.values():
        if entry.get("kind") == "plugin" and re.fullmatch(r"[0-9a-f]{40}", entry.get("commit", "")):
            name = entry["spec"]["plugin"]
            plugins.setdefault(name, {
                "name": name,
                "description": entry.get("summary", ""),
                "source": {"source": "github", "repo": entry["repo"], "sha": entry["commit"]},
                "homepage": entry["url"],
            })
    if plugins:
        market["plugins"] = sorted(plugins.values(), key=lambda p: p["name"])
        MARKET_PATH.parent.mkdir(exist_ok=True)
        MARKET_PATH.write_text(json.dumps(market, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    if report["queued"]:
        lines += ["### Installs automatically once merged", ""]
        lines += [f"- {e['repo']}: {describe(e)}" for e in report["queued"]]
        lines += ["", "They install in the background the next time you open Claude Code "
                      "(or run `python star-triage.py sync`)."]
    if report["available"]:
        lines += ["", "### Can be installed (tick `approve` on the note in Obsidian)", ""]
        lines += [f"- {e['repo']}: {describe(e)}" for e in report["available"]]
    if report["blocked"]:
        lines += ["", "### Blocked from automatic install", ""]
        lines += [f"- {e['repo']}: {e['blocked']}. Read its Security scan section." for e in report["blocked"]]
    if report["manual"]:
        lines += ["", "### Install by hand", ""]
        lines += [f"- {e['repo']}: {e['problem']}." for e in report["manual"]]
    (WORK_DIR / "install-summary.md").write_text("\n".join(lines).strip() + "\n" if lines else "", encoding="utf-8")


if __name__ == "__main__":
    main()
