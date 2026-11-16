#!/usr/bin/env python3
"""Runs the seal and restore steps of action.yml as written, in bash, against a temp git repo.
Offline. Run: python tests/test_action_steps.py

  UT-12  the restore step names settings that were added, removed or whose value changed
         (e.g. an origin URL rewritten with a token), never prints a value, and puts the
         clean .git/config back. Tampering with the trusted copy still fails the run.
  SEC-20 both Claude steps set allowed_bots (so claude-code-action keeps the token out of
         .git/config) and allow no shell; a token URL left in .git/config raises a warning
         without printing it.
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


def claude_steps() -> list:
    steps = yaml.safe_load((ENGINE / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    return [s for s in steps if "claude-code-action" in str(s.get("uses", ""))]


def main() -> None:
    cs = claude_steps()
    check("SEC-20 there are two Claude steps", len(cs) == 2, str(len(cs)))
    for s in cs:
        w = s.get("with", {})
        args = str(w.get("claude_args", ""))
        allowed = args.split("--allowedTools", 1)[1].split("--", 1)[0] if "--allowedTools" in args else ""
        check(f"SEC-20 {s.get('name')}: allowed_bots is set", bool(str(w.get("allowed_bots", "")).strip()))
        check(f"SEC-20 {s.get('name')}: no shell for Claude",
              '--disallowedTools "Bash' in args and allowed and "Bash" not in allowed, args)
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
        check("SEC-20 a token URL in .git/config raises a warning", "::warning title=Token in .git/config" in log,
              log[-300:])
        check("restore puts the clean .git/config back",
              (repo / ".git" / "config").read_text(encoding="utf-8") == clean)

        res = bash(step("Restore .git/config"), repo, {**env, "BEFORE": seal})
        check("an untouched .git/config logs nothing", res.returncode == 0 and "Restoring" not in res.stdout
              and "::warning" not in res.stdout, res.stdout[-200:])
        # What claude-code-action does with allowed_bots set: a username-only URL and a helper.
        subprocess.run(["git", "config", "remote.origin.url", "https://x-access-token@github.com/o/r.git"],
                       cwd=repo, check=True)
        res = bash(step("Restore .git/config"), repo, {**env, "BEFORE": seal})
        check("SEC-20 a username-only URL (credential helper) raises no warning",
              res.returncode == 0 and "::warning" not in res.stdout, res.stdout[-200:])
        (temp / "trusted" / "new.json").write_text('[{"repo": "evil"}]', encoding="utf-8")
        res = bash(step("Restore .git/config"), repo, {**env, "BEFORE": seal})
        check("tampering with the trusted copy still fails the run",
              res.returncode != 0 and "Integrity check failed" in res.stdout, res.stdout[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
