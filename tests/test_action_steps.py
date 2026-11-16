#!/usr/bin/env python3
"""Runs the seal and restore steps of action.yml as written, in bash, against a temp git repo.
Offline. Run: python tests/test_action_steps.py

  UT-12  the restore step names settings that were added, removed or whose value changed
         (e.g. an origin URL rewritten with a token), never prints a value, and puts the
         clean .git/config back. Tampering with the trusted copy still fails the run.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ENGINE = Path(__file__).resolve().parent.parent
failed = False
SECRET = "ghs_FAKEtoken1234567890"


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail and not ok else ""))
    failed = failed or not ok


def step(prefix: str) -> str:
    steps = yaml.safe_load((ENGINE / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    return next(s["run"] for s in steps if str(s.get("name", "")).startswith(prefix))


def bash(script: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    # GitHub runs `shell: bash` as: bash --noprofile --norc -eo pipefail {0}
    path = cwd.parent / "step.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)], cwd=cwd,
                          env=env, capture_output=True, text=True, encoding="utf-8")


def main() -> None:
    tmp = Path(tempfile.mkdtemp()).resolve()
    try:
        repo, temp = tmp / "ws", tmp / "runner"
        (temp / "trusted").mkdir(parents=True)
        (temp / "trusted" / "new.json").write_text("[]", encoding="utf-8")
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=repo, check=True)
        clean = (repo / ".git" / "config").read_text(encoding="utf-8")
        out = tmp / "github_output"
        env = {**os.environ, "RUNNER_TEMP": temp.as_posix(), "GITHUB_OUTPUT": out.as_posix(),
               "GITHUB_STEP_SUMMARY": (tmp / "summary").as_posix()}

        res = bash(step("Record hashes"), repo, env)
        seal = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line).get("sum", "")
        check("seal step runs and records a sum", res.returncode == 0 and len(seal) == 64, res.stderr[-300:])

        # What claude-code-action does: git user added, origin URL rewritten with a token.
        for args in (["user.name", "bot"], ["remote.origin.url", f"https://x-access-token:{SECRET}@github.com/o/r.git"]):
            subprocess.run(["git", "config", *args], cwd=repo, check=True)
        subprocess.run(["git", "config", "--unset", "remote.origin.fetch"], cwd=repo, check=True)
        res = bash(step("Restore .git/config"), repo, {**env, "BEFORE": seal})
        log = res.stdout + res.stderr
        check("restore passes when only .git/config changed", res.returncode == 0, log[-300:])
        check("restore names the added setting", "added: user.name" in log, log[-300:])
        check("restore names the removed setting", "removed: remote.origin.fetch" in log, log[-300:])
        check("restore names a setting whose value changed", "value changed: remote.origin.url" in log, log[-300:])
        check("restore never prints a value", SECRET not in log and "x-access-token" not in log)
        check("restore puts the clean .git/config back",
              (repo / ".git" / "config").read_text(encoding="utf-8") == clean)

        res = bash(step("Restore .git/config"), repo, {**env, "BEFORE": seal})
        check("an untouched .git/config logs nothing", res.returncode == 0 and "Restoring" not in res.stdout,
              res.stdout[-200:])
        (temp / "trusted" / "new.json").write_text('[{"repo": "evil"}]', encoding="utf-8")
        res = bash(step("Restore .git/config"), repo, {**env, "BEFORE": seal})
        check("tampering with the trusted copy still fails the run",
              res.returncode != 0 and "Integrity check failed" in res.stdout, res.stdout[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
