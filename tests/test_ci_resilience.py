#!/usr/bin/env python3
"""Checks that one bad repo or one bad note does not stop a run. Offline, temp folder only.
Run: python tests/test_ci_resilience.py

  CI-1  malformed package.json / .mcp.json / marketplace.json do not crash the scanner;
        a repo the scanner cannot read gets a High floor and a "scan failed" blocker.
  CI-4  one bad note plus one good note: the bad one is moved to .triage/rejected/, the
        good one survives. Only bad notes: the run fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
SCRIPTS = ENGINE / "scripts"
failed = False

GOOD = ('---\ntype: "Skill"\ncategory: ["Workflow"]\nstatus: "Inbox"\nverdict: "Skip"\nrisk: "Low"\n'
        'saves_tokens: "No"\nrelevant_to: ["General"]\nrepo: "https://github.com/o/good"\nsummary: "x"\n'
        'install: "none"\nlicence: "MIT"\nsource: "GitHub star"\nadded: 2026-01-01\nreviewed: 2026-01-01\n'
        '---\n\n# good\n\n## What it is\n\nx\n\n## Verdict\n\nx\n')
BAD = '---\nrepo: "https://github.com/o/bad\nverdict: [unclosed\n---\n\n# bad\n'


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail and not ok else ""))
    failed = failed or not ok


def run(script: str, *args: str, root: Path, trusted: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "STAR_TRIAGE_DATA": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    if trusted:
        env["STAR_TRIAGE_TRUSTED"] = str(trusted)
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], env=env, capture_output=True,
                          text=True, encoding="utf-8")


def data_repo(root: Path) -> None:
    (root / "tracker").mkdir(parents=True)
    shutil.copy(ENGINE / "template" / "config.yml", root / "config.yml")
    shutil.copy(ENGINE / "template" / ".gitignore", root / ".gitignore")


def scan_cases(tmp: Path) -> None:
    root = tmp / "scan"
    data_repo(root)
    odd = root / ".triage" / "repos" / "o__odd"
    for rel, text in {"package.json": "[1, 2]", "a/package.json": '{"scripts": "x"}',
                      "b/package.json": '{"scripts": {"postinstall": 5}}',
                      ".mcp.json": '{"mcpServers": ["x"], "s": {"args": 5, "url": {"a": 1}}}',
                      ".claude-plugin/marketplace.json": '{"plugins": 5}'}.items():
        (odd / rel).parent.mkdir(parents=True, exist_ok=True)
        (odd / rel).write_text(text, encoding="utf-8")
    res = run("scan_repo.py", str(odd), root=root)
    check("CI-1 malformed package.json, .mcp.json and marketplace.json do not crash the scan",
          res.returncode == 0 and '"risk_floor"' in res.stdout, res.stderr[-300:])

    # A folder named README.md cannot be read as a file, so this repo's scan raises.
    broken = root / ".triage" / "repos" / "o__broken"
    (broken / "README.md").mkdir(parents=True)
    batch = [{"repo": f"o/{n}", "note_path": f"tracker/o__{n}.md", "local_path": f".triage/repos/o__{n}",
              "cloned": True, "commit": "a" * 40, "license": "MIT"} for n in ("broken", "odd")]
    (root / ".triage" / "new.json").write_text(json.dumps(batch), encoding="utf-8")
    res = run("scan_repo.py", root=root)
    out = root / ".triage" / "scan"
    scan = json.loads((out / "o__broken.json").read_text(encoding="utf-8")) if (out / "o__broken.json").exists() else {}
    check("CI-1 a repo that cannot be scanned gets a High floor and a blocker, and the run goes on",
          res.returncode == 0 and scan.get("risk_floor") == "High" and scan.get("kind") == "unknown"
          and str((scan.get("install_blockers") or [""])[0]).startswith("scan failed: ")
          and (out / "o__odd.json").exists(), res.stderr[-300:] or json.dumps(scan)[:300])


def note_case(root: Path, notes: dict[str, str]) -> subprocess.CompletedProcess:
    data_repo(root)
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)
    trusted = root.parent / (root.name + "-trusted")
    trusted.mkdir()
    batch = [{"repo": f"o/{n}", "id": i + 1, "url": f"https://github.com/o/{n}", "note_path": f"tracker/o__{n}.md"}
             for i, n in enumerate(notes)]
    (trusted / "new.json").write_text(json.dumps(batch), encoding="utf-8")
    for name, text in notes.items():
        (root / "tracker" / f"o__{name}.md").write_text(text, encoding="utf-8")
    res = run("apply_scan.py", root=root, trusted=trusted)
    if res.returncode != 0:
        return res
    return run("validate_notes.py", "--changed", root=root, trusted=trusted)


def note_cases(tmp: Path) -> None:
    root = tmp / "mixed"
    res = note_case(root, {"good": GOOD, "bad": BAD})
    good = root / "tracker" / "o__good.md"
    check("CI-4 one bad note plus one good note: the run passes and the good note survives",
          res.returncode == 0 and good.exists(), (res.stdout + res.stderr)[-300:])
    check("CI-4 the bad note is moved to .triage/rejected/ with a warning",
          not (root / "tracker" / "o__bad.md").exists() and (root / ".triage" / "rejected" / "o__bad.md").exists()
          and "::warning" in res.stdout and "o__bad.md" in res.stdout, res.stdout[-300:])
    check("CI-2 the note carries the star's id from the trusted new.json",
          good.exists() and "repo_id: 1" in good.read_text(encoding="utf-8"))

    res = note_case(tmp / "allbad", {"bad": BAD})
    check("CI-4 only bad notes: the run fails", res.returncode != 0, res.stdout[-300:])


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        scan_cases(tmp)
        note_cases(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
