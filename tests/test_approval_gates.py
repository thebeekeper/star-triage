#!/usr/bin/env python3
"""Checks two approval gates offline, in a temp folder. Run: python tests/test_approval_gates.py

  SEC-3  `approve: true` written into a note by the reviewer never approves an install;
         a re-review keeps only the owner's earlier tick (from the trusted new.json).
  SEC-4  a setup command must be one of the package's own command names: python3.12,
         bash.exe and /bin/sh are rejected.
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


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail and not ok else ""))
    failed = failed or not ok


def run_case(root: Path, item_extra: dict) -> tuple[str, dict]:
    """Claude 'writes' a note with approve: true for a repo whose verdict is not Install.
    Returns the note text and its installs.json entry after apply_scan + queue_installs."""
    shutil.rmtree(root, ignore_errors=True)
    trusted = root / "trusted"
    (root / "tracker").mkdir(parents=True)
    (root / ".triage" / "proposals").mkdir(parents=True)
    (trusted / "scan").mkdir(parents=True)
    shutil.copy(ENGINE / "template" / "config.yml", root / "config.yml")
    (root / "tracker" / "o__r.md").write_text(
        '---\nrepo: "https://github.com/o/r"\nverdict: "Skip"\nrisk: "Low"\nsummary: "x"\n'
        'approve: true\nauto_install: "queued"\nsneaky: "yes"\n---\n\n# r\n\n## Install\n\nnone\n', encoding="utf-8")
    (root / ".triage" / "proposals" / "o__r.json").write_text('{"kind": "skill"}', encoding="utf-8")
    item = {"repo": "o/r", "url": "https://github.com/o/r", "note_path": "tracker/o__r.md", **item_extra}
    (trusted / "new.json").write_text(json.dumps([item]), encoding="utf-8")
    (trusted / "scan" / "o__r.json").write_text(json.dumps({
        "commit": "a" * 40, "risk_floor": "Low", "findings": [], "install_blockers": [],
        "skill_list": [{"name": "demo", "path": "skills/demo"}]}), encoding="utf-8")
    env = {**os.environ, "STAR_TRIAGE_DATA": str(root), "STAR_TRIAGE_TRUSTED": str(trusted),
           "GITHUB_REPOSITORY_OWNER": "o", "PYTHONDONTWRITEBYTECODE": "1"}
    for script in ("apply_scan.py", "queue_installs.py"):
        res = subprocess.run([sys.executable, str(SCRIPTS / script)], env=env, capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"{script} failed:\n{res.stdout}\n{res.stderr}")
    entry = json.loads((root / "installs.json").read_text(encoding="utf-8"))["items"]["o/r"]
    return (root / "tracker" / "o__r.md").read_text(encoding="utf-8"), entry


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        note, entry = run_case(tmp / "new", {})
        check("SEC-3 new note: approve written by Claude is not an approval",
              "approve: false" in note and entry["approved"] is False and entry["status"] == "available", note[:300])
        check("SEC-3 new note: keys Claude may not set are dropped",
              "sneaky" not in note and 'auto_install: "available"' in note, note[:300])

        note, entry = run_case(tmp / "re", {"retriage": True, "previous": {"status": "Inbox"}})
        check("SEC-3 re-review, owner never ticked: stays unapproved",
              "approve: false" in note and entry["approved"] is False, note[:300])

        note, entry = run_case(tmp / "kept", {"retriage": True, "previous": {"approve": True}})
        check("SEC-3 re-review, owner had ticked: their tick is kept", "approve: true" in note, note[:300])

        os.environ["STAR_TRIAGE_DATA"] = str(tmp / "new")
        sys.path.insert(0, str(SCRIPTS))
        from queue_installs import check_setup
        scan = {"readme_commands": ["tool init", "python3.12 -m tool init", "bash.exe install.sh",
                                    "/bin/sh install.sh", "bash install.sh"]}
        own = ["tool", "tool-cli"]
        check("SEC-4 the package's own command is accepted", check_setup(["tool", "init"], scan, own) is None)
        for bad in (["python3.12", "-m", "tool", "init"], ["bash.exe", "install.sh"], ["/bin/sh", "install.sh"],
                    ["bash", "install.sh"]):
            check(f"SEC-4 {bad[0]} is rejected", check_setup(bad, scan, own) is not None)
        # Even a package that names one of its commands like a path or a file gets nowhere.
        check("SEC-4 a path is rejected even if the package lists it",
              check_setup(["/bin/sh", "install.sh"], scan, ["/bin/sh"]) is not None)
        check("SEC-4 an extension is rejected even if the package lists it",
              check_setup(["bash.exe", "install.sh"], scan, ["bash.exe"]) is not None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
