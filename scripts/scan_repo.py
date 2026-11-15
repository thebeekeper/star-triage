"""Deterministic security scan of the cloned repos in .triage/new.json.

This is plain code, not an AI, so text inside a repo cannot talk it out of a finding.
For each repo it writes .triage/scan/<stem>.json with:
  kind            plugin | marketplace | skill | other
  plugin          {"name": ...} when the repo root is an installable plugin
  findings        [{id, severity, title, file, line, detail}]
  risk_floor      Low | Medium | High  (the note's risk can be raised, never lowered)
  auto_install    {"eligible": bool, "reasons": [...]}

Usage: python scripts/scan_repo.py            (all repos in .triage/new.json)
       python scripts/scan_repo.py <path>     (one directory, prints JSON)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from common import ROOT, WORK_DIR

SEVERITY_ORDER = {"info": 0, "medium": 1, "high": 2}
RISK_FROM_SEVERITY = {0: "Low", 1: "Medium", 2: "High"}
MAX_HITS_PER_CHECK = 5
MAX_SCAN_BYTES = 1_000_000

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh",
            ".ps1", ".psm1", ".bat", ".cmd", ".rb", ".go", ".rs", ".php", ".pl", ".lua"}
CONFIG_EXT = {".json", ".jsonc", ".yaml", ".yml", ".toml"}
DOC_EXT = {".md", ".mdx", ".txt", ".rst"}
BINARY_EXT = {".exe", ".dll", ".so", ".dylib", ".bin", ".node", ".msi", ".app", ".jar"}
BINARY_MAGIC = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
BENIGN_DOMAINS = {"github.com", "raw.githubusercontent.com", "api.github.com", "localhost",
                  "127.0.0.1", "example.com", "www.w3.org", "json-schema.org", "schema.org",
                  "npmjs.com", "www.npmjs.com", "registry.npmjs.org", "pypi.org",
                  "docs.anthropic.com", "docs.claude.com", "code.claude.com", "claude.ai",
                  "anthropic.com", "www.anthropic.com", "opensource.org", "shields.io",
                  "img.shields.io", "creativecommons.org", "www.apache.org", "spdx.org"}

PIPE_TO_SHELL = re.compile(
    r"(curl|wget)\b[^\n|]{0,300}\|\s*(sudo\s+)?(ba|z|da)?sh\b"
    r"|(iwr|irm|invoke-webrequest|invoke-restmethod)\b[^\n|]{0,300}\|\s*iex\b", re.I)
AGENT_CONFIG = re.compile(r"(~|homedir\(\)|home\(\)|\$HOME|%USERPROFILE%|USERPROFILE)[^\n]{0,60}\.claude\b"
                          r"|\.claude[/\\](settings(\.local)?\.json|CLAUDE\.md|skills|agents|commands|hooks)"
                          r"|['\"/\\]CLAUDE\.md['\"]|['\"/\\]AGENTS\.md['\"]", re.I)
WRITE_OP = re.compile(r"write(File|FileSync|_text|_bytes)?\(|open\([^)]*['\"][wa]\+?['\"]|(?<![-=>])>>?\s*['\"$~]"
                      r"|Set-Content|Add-Content|Out-File|appendFile|copyFile|fs\.cp|shutil\.copy", re.I)
PERMISSION_BYPASS = re.compile(r"--dangerously-skip-permissions|bypassPermissions|"
                               r"dangerouslyDisableSandbox|--permission-mode\s+bypass", re.I)
DESTRUCTIVE = re.compile(r"\brm\s+-rf\s+(/|~|\$HOME)(\s|$|/\*)|\bchmod\s+(-R\s+)?777\b|\bmkfs\b|"
                         r"Remove-Item\s+[^\n]*-Recurse[^\n]*\$env:USERPROFILE", re.I)
SUDO = re.compile(r"(^|[\s;&|`(])sudo\s+\S")
DECODE_EXEC = re.compile(r"((?<![.\w])(eval|exec)\s*\(|new\s+Function\s*\()[^\n]{0,200}"
                         r"(atob|b64decode|base64|fromCharCode|Buffer\.from\([^)]*base64)", re.I)
LONG_B64 = re.compile(r"['\"][A-Za-z0-9+/]{300,}={0,2}['\"]")
TELEMETRY = re.compile(r"\b(posthog|mixpanel|segment\.io|amplitude|sentry_sdk|@sentry/|"
                       r"google-analytics|gtag\()", re.I)
NETWORKY = re.compile(r"download|fetch\(|https?://|npm (i|install)|pip install|urlopen|requests\.get", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|fixtures?|examples?|e2e|testdata|mocks?|benchmarks?|bench)(/|$)"
                       r"|\.(test|spec)\.[a-z]+$", re.I)
AUTO_UPDATE = re.compile(r"auto[-_]?update|self[-_]?update|autoUpdate|selfUpdate", re.I)
URL = re.compile(r"https?://[^\s'\"<>()`\]]+")
INJECTION = re.compile(
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) instructions"
    r"|disregard (all |any )?(the )?(previous|prior|above) (instructions|rules)"
    r"|do not (tell|inform|mention (this )?to|alert) the user"
    r"|without (asking|telling|informing) the user"
    r"|(you are|you're) now (in )?(developer|jailbreak|unrestricted|god) mode"
    r"|reveal (your|the) system prompt"
    r"|(always|must) follow (these|this|the following) (rules|instructions) strictly", re.I)
ALLOWED_TOOLS_BROAD = re.compile(r"^allowed-tools:\s*.*\bBash\b(?!\()|^allowed-tools:.*Bash\(\s*\*\s*\)", re.I | re.M)


class Scan:
    def __init__(self, root: Path):
        self.root = root
        self.findings: list[dict] = []
        self.hits: dict[str, int] = defaultdict(int)
        self.domains: dict[str, int] = defaultdict(int)
        self.urls: set[str] = set()

    def add(self, fid: str, severity: str, title: str, file: Path | None = None,
            line: int | None = None, detail: str = "") -> None:
        self.hits[fid] += 1
        if self.hits[fid] > MAX_HITS_PER_CHECK:
            return
        rel = str(file.relative_to(self.root)).replace("\\", "/") if file and file != self.root else None
        if rel:  # a file name that is not valid UTF-8 must not break the note later
            rel = rel.encode("utf-8", errors="replace").decode("utf-8")
        if rel and TEST_PATH.search(rel) and severity != "info":
            severity, title = "info", title + " (in tests, examples or benchmarks)"
        self.findings.append({"id": fid, "severity": severity, "title": title, "file": rel,
                              "line": line, "detail": detail[:200]})


MAX_URLS = 5000


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def iter_hook_commands(node, event=None):
    """Yield (event, command) from any hooks structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            ev = k if k[:1].isupper() and k not in ("Bash",) else event
            if k == "command" and isinstance(v, str):
                yield event, v
            else:
                yield from iter_hook_commands(v, ev)
    elif isinstance(node, list):
        for item in node:
            yield from iter_hook_commands(item, event)


def readme_commands(root: Path) -> list[str]:
    """Lines inside fenced code blocks of the top-level README, whitespace-normalised.
    A post-install setup command must be one of these (or start one)."""
    out: list[str] = []
    for readme in sorted(root.glob("README*.md"))[:3]:
        inside = False
        for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                cmd = " ".join(line.split("#", 1)[0].replace("$ ", "", 1).split())
                if cmd and len(out) < 500:
                    out.append(cmd)
    return out


def scan_claude_structure(s: Scan, result: dict, meta_name: str | None = None) -> None:
    root = s.root
    plugin_json = load_json(root / ".claude-plugin" / "plugin.json")
    market_json = load_json(root / ".claude-plugin" / "marketplace.json")
    skills = [p for p in root.rglob("SKILL.md")]

    if isinstance(plugin_json, dict):
        result["kind"] = "plugin"
        result["plugin"] = {"name": plugin_json.get("name"), "version": plugin_json.get("version")}
    if isinstance(market_json, dict):
        plugins = market_json.get("plugins")
        entries = [p for p in plugins if isinstance(p, dict)] if isinstance(plugins, list) else []
        result["marketplace"] = [{"name": p.get("name"), "source": p.get("source")} for p in entries]
        if result["kind"] != "plugin":
            result["kind"] = "marketplace"
        for p in entries:
            src = p.get("source")
            if isinstance(src, dict) and src.get("source") not in ("github", "url", "git-subdir"):
                s.add("marketplace-command-source", "high",
                      "Marketplace entry installs by running a command", root / ".claude-plugin" / "marketplace.json",
                      detail=json.dumps(src)[:200])
            if src in ("./", ".", "./.") and not result.get("plugin"):
                result["plugin"] = {"name": p.get("name"), "version": p.get("version")}
    if result["kind"] == "other" and skills:
        result["kind"] = "skill"
    result["skills"] = [str(p.parent.relative_to(root)).replace("\\", "/") for p in skills][:20]
    # Skills a user could install: real SKILL.md folders, not tests or examples.
    skill_list = []
    for sk in skills:
        rel = str(sk.parent.relative_to(root)).replace("\\", "/")
        if TEST_PATH.search(rel + "/"):
            continue
        m = re.search(r"^name:\s*['\"]?([A-Za-z0-9][\w.-]{0,63})['\"]?\s*$",
                      sk.read_text(encoding="utf-8", errors="ignore")[:2000], re.M)
        fallback = (meta_name or root.name) if rel == "." else sk.parent.name
        skill_list.append({"path": rel, "name": (m.group(1) if m else fallback).lower()})
    # One copy per name: prefer visible folders (adapters often keep copies in .hidden dirs).
    skill_list.sort(key=lambda k: (any(part.startswith(".") for part in k["path"].split("/")),
                                   k["path"].count("/"), k["path"]))
    seen: set[str] = set()
    result["skill_list"] = [k for k in skill_list if not (k["name"] in seen or seen.add(k["name"]))][:20]

    # Hooks: plugin.json "hooks" (inline or path) and hooks/hooks.json
    hook_sources = []
    if isinstance(plugin_json, dict) and plugin_json.get("hooks"):
        h = plugin_json["hooks"]
        if isinstance(h, str):
            hook_sources.append((root / h.lstrip("./"), load_json(root / h.lstrip("./"))))
        else:
            hook_sources.append((root / ".claude-plugin" / "plugin.json", h))
    for candidate in (root / "hooks" / "hooks.json", root / ".claude" / "settings.json"):
        if candidate.exists():
            hook_sources.append((candidate, load_json(candidate)))
    hooks, seen_paths = [], set()
    for path, data in hook_sources:
        if path.resolve() in seen_paths:
            continue
        seen_paths.add(path.resolve())
        if data is None:
            continue
        for event, cmd in iter_hook_commands(data.get("hooks", data) if isinstance(data, dict) else data):
            hooks.append({"event": event, "command": cmd[:200], "file": str(path.relative_to(root))})
            network = re.search(r"\b(curl|wget|iwr|irm|Invoke-WebRequest|http)", cmd, re.I)
            s.add("hook", "high" if network else "medium",
                  f"Hook runs automatically on {event or 'an event'}" + (" and reaches the network" if network else ""),
                  path, detail=cmd)
    result["hooks"] = hooks[:20]

    # MCP servers: plugin.json mcpServers, .mcp.json
    mcp = {}
    if isinstance(plugin_json, dict) and isinstance(plugin_json.get("mcpServers"), dict):
        mcp.update(plugin_json["mcpServers"])
    mcp_file = load_json(root / ".mcp.json")
    if isinstance(mcp_file, dict):
        listed = mcp_file.get("mcpServers", mcp_file)
        if isinstance(listed, dict):
            mcp.update(listed)
    servers = []
    for name, cfg in mcp.items():
        if not isinstance(cfg, dict):
            continue
        args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
        cmd = " ".join([str(cfg.get("command", ""))] + [str(a) for a in args]).strip()
        url = cfg.get("url") if isinstance(cfg.get("url"), str) else ""
        servers.append({"name": name, "command": cmd[:200], "url": url})
        s.add("mcp-server", "medium", f"Starts MCP server '{name}' that Claude can call",
              root / (".mcp.json" if mcp_file else ".claude-plugin/plugin.json"), detail=cmd or url)
        if re.search(r"\bnpx\b", cmd) and not re.search(r"@\d", cmd):
            s.add("mcp-unpinned", "medium", f"MCP server '{name}' runs an unpinned npx package (always latest)",
                  None, detail=cmd)
    result["mcp_servers"] = servers

    # package.json lifecycle scripts
    for pkg in root.rglob("package.json"):
        if "node_modules" in pkg.parts:
            continue
        data = load_json(pkg)
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            continue
        for hook in ("preinstall", "install", "postinstall", "prepare"):
            cmd = scripts.get(hook)
            if cmd and isinstance(cmd, str):
                risky = re.search(r"curl|wget|http|node\s+-e|eval|\|\s*sh", cmd, re.I)
                sev = "high" if risky else ("info" if hook == "prepare" else "medium")
                s.add("install-script", sev,
                      f"npm '{hook}' script runs on install", pkg, detail=cmd)

    # Skills with unrestricted shell
    for sk in skills:
        head = sk.read_text(encoding="utf-8", errors="ignore")[:3000]
        if ALLOWED_TOOLS_BROAD.search(head):
            s.add("skill-broad-bash", "medium", "Skill pre-approves unrestricted Bash (allowed-tools)", sk)


def scan_files(s: Scan) -> None:
    for path in s.root.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts:
            continue
        ext = path.suffix.lower()
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                head = f.read(4)
        except OSError:
            continue
        if ext in BINARY_EXT or head.startswith(BINARY_MAGIC):
            s.add("binary", "high", "Ships a compiled executable or library", path)
            continue
        is_code = ext in CODE_EXT
        if not is_code and ext == "" and head.startswith(b"#!"):
            is_code = True
        is_doc = ext in DOC_EXT
        is_config = ext in CONFIG_EXT
        if not (is_code or is_doc or is_config) or size > MAX_SCAN_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if len(s.urls) < MAX_URLS:
            for url in URL.findall(text):
                if url.startswith("https://") and len(s.urls) < MAX_URLS:
                    s.urls.add(url.split("#")[0].split("?")[0].rstrip(".,;:'\"*"))
        for n, line in enumerate(text.splitlines(), 1):
            if len(line) > 5000:
                if is_code:
                    s.add("minified", "medium", "Very long line in code (minified or obfuscated)", path, n)
                line = line[:5000]
            if PIPE_TO_SHELL.search(line):
                s.add("pipe-to-shell" if is_code else "pipe-to-shell-docs",
                      "high" if is_code else "medium",
                      "Downloads and runs a script in one step" + ("" if is_code else " (in docs or data files)"),
                      path, n, line.strip())
            if is_doc:
                if INJECTION.search(line):
                    s.add("injection-text", "medium", "Text that tries to instruct an AI agent", path, n, line.strip())
                continue
            if PERMISSION_BYPASS.search(line):
                s.add("permission-bypass", "high", "Disables Claude Code permission checks", path, n, line.strip())
            if DESTRUCTIVE.search(line):
                s.add("destructive", "high", "Destructive command on the home or root folder", path, n, line.strip())
            if is_code and SUDO.search(line):
                s.add("sudo", "medium", "Runs or suggests commands with sudo", path, n, line.strip())
            if is_code and AGENT_CONFIG.search(line):
                writes = WRITE_OP.search(line)
                s.add("agent-config-write" if writes else "agent-config",
                      "high" if writes else "medium",
                      "Writes to Claude/agent config files" if writes else "Reads or references Claude/agent config files",
                      path, n, line.strip())
            if is_code and DECODE_EXEC.search(line):
                s.add("decode-exec", "high", "Executes decoded or obfuscated code", path, n, line.strip())
            if is_code and LONG_B64.search(line):
                s.add("encoded-blob", "medium", "Large encoded blob in code", path, n)
            if is_code and TELEMETRY.search(line):
                s.add("telemetry", "medium", "Telemetry or analytics code", path, n, line.strip())
            if is_code and AUTO_UPDATE.search(line):
                s.add("auto-update", "medium" if NETWORKY.search(line) else "info",
                      "Auto-update behaviour", path, n, line.strip())
            if is_code:
                for url in URL.findall(line):
                    try:
                        host = (urlparse(url).hostname or "").lower()
                    except ValueError:
                        continue
                    if host and host not in BENIGN_DOMAINS and not host.endswith(".github.io"):
                        s.domains[host] += 1


def add_osv(s: Scan, osv_path: Path) -> dict | None:
    data = load_json(osv_path)
    if not isinstance(data, dict):
        return None
    vulns, worst = [], 0.0
    for res in data.get("results", []) or []:
        for pkg in res.get("packages", []) or []:
            name = (pkg.get("package") or {}).get("name")
            for v in pkg.get("vulnerabilities", []) or []:
                vulns.append(f"{name}: {v.get('id')}")
            for g in pkg.get("groups", []) or []:
                try:
                    worst = max(worst, float(g.get("max_severity") or 0))
                except ValueError:
                    pass
    if vulns:
        sev = "high" if worst >= 9.0 else "medium"
        s.add("vulnerable-deps", sev, f"{len(vulns)} known vulnerabilities in dependencies (OSV)",
              detail=", ".join(vulns[:8]) + (f"; worst CVSS {worst}" if worst else ""))
    return {"vulnerabilities": len(vulns), "worst_cvss": worst or None}


def scan_repo(root: Path, meta: dict | None = None, osv_path: Path | None = None) -> dict:
    s = Scan(root)
    result: dict = {"kind": "other", "plugin": None, "skills": [], "hooks": [], "mcp_servers": []}
    scan_claude_structure(s, result, ((meta or {}).get("repo") or "").split("/")[-1] or None)
    result["readme_commands"] = readme_commands(root)
    scan_files(s)
    result["osv"] = add_osv(s, osv_path) if osv_path and osv_path.exists() else None
    meta = meta or {}
    if meta.get("archived"):
        s.add("archived", "info", "Repository is archived (no longer maintained)")
    if not meta.get("license") or meta.get("license") in ("NOASSERTION", "NONE"):
        s.add("no-license", "info", "No recognised open-source licence")
    if s.domains:
        top = sorted(s.domains.items(), key=lambda kv: -kv[1])[:10]
        s.add("outbound-domains", "info", "Code contacts these domains",
              detail=", ".join(f"{d} ({c})" for d, c in top))

    worst = max((SEVERITY_ORDER[f["severity"]] for f in s.findings), default=0)
    result["risk_floor"] = RISK_FROM_SEVERITY[worst]
    result["findings"] = sorted(s.findings, key=lambda f: -SEVERITY_ORDER[f["severity"]])
    result["counts"] = {sev: sum(1 for f in s.findings if f["severity"] == sev) for sev in SEVERITY_ORDER}

    reasons = []
    if not (result.get("plugin") and result["plugin"].get("name")):
        reasons.append("not a Claude Code plugin at the repository root")
    if worst >= SEVERITY_ORDER["high"]:
        reasons.append("high-severity findings")
    if any(f["id"] == "binary" for f in s.findings):
        reasons.append("ships compiled binaries")
    if not meta.get("commit"):
        reasons.append("reviewed commit unknown")
    result["auto_install"] = {"eligible": not reasons, "reasons": reasons}
    # Reasons that block installing anything from this repo, whatever its kind.
    result["install_blockers"] = [r for r in reasons if r != "not a Claude Code plugin at the repository root"]
    # Every https URL in the repo (README included). An MCP server URL must appear here.
    result["urls"] = sorted(s.urls)
    result["commit"] = meta.get("commit")
    return result


def main() -> None:
    if len(sys.argv) > 1:
        print(json.dumps(scan_repo(Path(sys.argv[1]).resolve()), indent=2))
        return
    batch = json.loads((WORK_DIR / "new.json").read_text(encoding="utf-8"))
    out_dir = WORK_DIR / "scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in batch:
        stem = Path(item["note_path"]).stem
        if not item.get("cloned"):
            result = {"kind": "unknown", "findings": [], "risk_floor": "Medium",
                      "auto_install": {"eligible": False, "reasons": ["repository could not be cloned"]},
                      "install_blockers": ["repository could not be cloned"], "skill_list": [], "urls": [],
                      "counts": {}, "commit": None}
        else:
            try:
                result = scan_repo(ROOT / item["local_path"], item, WORK_DIR / "osv" / f"{stem}.json")
            except Exception as exc:  # one odd repo must not stop the run; treat it as High risk
                reason = f"scan failed: {type(exc).__name__}: {exc}"[:300]
                print(f"::warning::{item['repo']}: {reason}".replace("\n", " "))
                result = {"kind": "unknown", "findings": [], "risk_floor": "High",
                          "auto_install": {"eligible": False, "reasons": [reason]},
                          "install_blockers": [reason], "skill_list": [], "urls": [],
                          "counts": {}, "commit": item.get("commit")}
        (out_dir / f"{stem}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        c = result.get("counts", {})
        print(f"{item['repo']}: kind={result['kind']} floor={result['risk_floor']} "
              f"high={c.get('high', 0)} medium={c.get('medium', 0)} "
              f"auto_install={'yes' if result['auto_install']['eligible'] else 'no'}")


if __name__ == "__main__":
    main()
