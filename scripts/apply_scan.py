"""Merge the deterministic security scan into each new note (runs after Claude).

For every repo in this run it:
  - raises the note's `risk` to the scan's risk floor if Claude rated it lower,
  - records `scan_risk`, `commit` (the exact reviewed commit) and `auto_install`,
  - inserts an "## Security scan (automated)" section rendered from the scan report.

Reads new.json and the scan reports from TRUSTED_DIR, never from files Claude could edit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from common import RISK_ORDER, ROOT, TRUSTED_DIR, split_note, write_note

SEV_LABEL = {"high": "High", "medium": "Medium", "info": "Info"}
# The only front-matter keys taken from the note Claude wrote (the ones prompts/triage.md asks for).
CLAUDE_KEYS = {"type", "category", "status", "verdict", "risk", "saves_tokens", "relevant_to", "repo",
               "summary", "install", "licence", "source", "added", "reviewed", "tags"}
SECTION_RE =re.compile(r"\n## Security scan \(automated\)\n.*?(?=\n## |\Z)", re.S)


def render_section(scan: dict, raised_from: str | None) -> str:
    lines = ["## Security scan (automated)", ""]
    commit = scan.get("commit")
    lines.append(f"Plain-code scan of commit `{commit[:12]}`." if commit else "Plain-code scan.")
    lines.append(f"Kind: **{scan.get('kind', 'unknown')}**. Risk floor: **{scan.get('risk_floor', 'Medium')}**.")
    if raised_from:
        lines.append(f"Risk raised from {raised_from} to match the scan.")
    hooks = scan.get("hooks") or []
    if hooks:
        lines.append("Hooks: " + ", ".join(sorted({h.get("event") or "unknown" for h in hooks})) + ".")
    servers = scan.get("mcp_servers") or []
    if servers:
        lines.append("MCP servers: " + ", ".join(s["name"] for s in servers) + ".")
    osv = scan.get("osv")
    if osv:
        lines.append(f"Known dependency vulnerabilities (OSV): {osv.get('vulnerabilities', 0)}.")
    findings = [f for f in scan.get("findings", []) if f["severity"] != "info"]
    info = [f for f in scan.get("findings", []) if f["severity"] == "info"]
    if findings:
        lines += ["", "| Severity | Finding | Where |", "| --- | --- | --- |"]
        for f in findings[:15]:
            where = f["file"] or ""
            if f.get("line"):
                where += f":{f['line']}"
            title = f["title"].replace("|", "\\|")
            lines.append(f"| {SEV_LABEL[f['severity']]} | {title} | `{where}` |" if where
                         else f"| {SEV_LABEL[f['severity']]} | {title} | |")
    else:
        lines += ["", "No medium or high findings."]
    if info:
        lines += ["", "Also noted: " + "; ".join(
            (f["title"] + (f" ({f['detail']})" if f["id"] == "outbound-domains" else "")) for f in info[:6]) + "."]
    return "\n".join(lines) + "\n"


def insert_section(body: str, section: str) -> str:
    body = SECTION_RE.sub("", body)
    marker = "\n## Install"
    idx = body.find(marker)
    if idx == -1:
        return body.rstrip("\n") + "\n\n" + section
    return body[:idx].rstrip("\n") + "\n\n" + section + body[idx:]


def main() -> None:
    batch = json.loads((TRUSTED_DIR / "new.json").read_text(encoding="utf-8"))
    for item in batch:
        note = ROOT / item["note_path"]
        if not note.exists():
            print(f"no note written for {item['repo']}")
            continue
        scan_path = TRUSTED_DIR / "scan" / f"{Path(item['note_path']).stem}.json"
        scan = json.loads(scan_path.read_text(encoding="utf-8")) if scan_path.exists() else {
            "risk_floor": "Medium", "findings": [], "auto_install": {"eligible": False, "reasons": ["no scan report"]}}
        fm, body = split_note(note)
        # Everything else (approve, auto_install, commit...) is set by code, never by Claude.
        fm = {k: v for k, v in fm.items() if k in CLAUDE_KEYS}
        floor = scan.get("risk_floor", "Medium")
        current = str(fm.get("risk", "High"))
        raised_from = None
        if RISK_ORDER.get(current, 2) < RISK_ORDER[floor]:
            raised_from, fm["risk"] = current, floor
        fm["scan_risk"] = floor
        fm["commit"] = scan.get("commit") or item.get("commit") or ""
        # The star's own URL and id, so the next run recognises this note whatever Claude wrote.
        if item.get("url"):
            fm["repo"] = item["url"]
        if item.get("id"):
            fm["repo_id"] = item["id"]
        # A re-review keeps what the owner set: status, where it came from, when, approve.
        for key, value in (item.get("previous") or {}).items():
            fm[key] = value
        write_note(note, fm, insert_section(body, render_section(scan, raised_from)))
        print(f"{item['repo']}: risk={fm.get('risk')} floor={floor}")


if __name__ == "__main__":
    main()
