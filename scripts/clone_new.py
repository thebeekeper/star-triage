"""Shallow-clone each repo in .triage/new.json into .triage/repos/ for read-only review.

Nothing from the cloned repos is ever executed: no hooks, no submodules, no LFS,
symlinks stored as plain files, .git removed afterwards, large files dropped.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from common import ROOT, WORK_DIR

MAX_FILE_BYTES = 2_000_000
CLONE_TIMEOUT_S = 180


def clone(url: str, dest: Path) -> str | None:
    """Clone and return the exact commit SHA that was cloned (None on failure)."""
    if dest.exists():
        shutil.rmtree(dest)
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    cmd = ["git",
           "-c", "core.symlinks=false",
           "-c", "core.hooksPath=/dev/null",
           "-c", "protocol.file.allow=never",
           "clone", "--depth", "1", "--single-branch", "--no-tags",
           "--recurse-submodules=no", url, str(dest)]
    try:
        subprocess.run(cmd, check=True, env=env, timeout=CLONE_TIMEOUT_S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  clone failed: {url}: {getattr(e, 'stderr', b'')!r}"[:300])
        shutil.rmtree(dest, ignore_errors=True)
        return None
    sha = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    shutil.rmtree(dest / ".git", ignore_errors=True)
    dropped = 0
    for p in dest.rglob("*"):
        if p.is_file() and p.stat().st_size > MAX_FILE_BYTES:
            p.unlink()
            dropped += 1
    if dropped:
        (dest / "_STAR_TRIAGE_NOTE.txt").write_text(
            f"{dropped} files larger than {MAX_FILE_BYTES} bytes were removed before review.\n")
    return sha if len(sha) == 40 else None


def main() -> None:
    new_path = WORK_DIR / "new.json"
    batch = json.loads(new_path.read_text(encoding="utf-8"))
    for item in batch:
        url = item["url"]
        if not url.startswith("https://github.com/"):
            item["cloned"] = False
            continue
        print(f"cloning {item['repo']}")
        sha = clone(url + ".git", ROOT / item["local_path"])
        item["cloned"] = sha is not None
        item["commit"] = sha
    new_path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    print(f"cloned {sum(1 for i in batch if i.get('cloned'))}/{len(batch)}")


if __name__ == "__main__":
    main()
