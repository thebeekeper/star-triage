"""Safety checks for sync.py (audit items SEC-1 and SEC-6). Run: python tests/test_sync_safety.py

No network, no gh, no real ~/.claude: HOME, CLAUDE_CONFIG_DIR and the data repo all point into
one temp folder, and nothing is installed. The only "approved" item used is blocked, which the
background sync never installs, so even a regression cannot install anything.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
SYNC = ENGINE / "scripts" / "sync.py"
TMP = Path(tempfile.mkdtemp(prefix="star-triage-test-")).resolve()
CLAUDE = TMP / "home" / ".claude"
DATA = TMP / "data"
EVIL = TMP / "evil"
failed = 0


def check(label: str, ok: bool) -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    failed += 0 if ok else 1


def data_repo(path: Path, items: dict) -> None:
    (path / "tracker").mkdir(parents=True)
    (path / "config.yml").write_text("install_scope: user\n", encoding="utf-8")
    (path / "installs.json").write_text(json.dumps({"items": items}), encoding="utf-8")


def run_sync(flag: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("STAR_TRIAGE_DATA", "CLAUDE_PLUGIN_ROOT")}
    env.update(HOME=str(TMP / "home"), USERPROFILE=str(TMP / "home"), CLAUDE_CONFIG_DIR=str(CLAUDE))
    return subprocess.run([sys.executable, str(SYNC), flag], cwd=EVIL, env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60)


def tarball(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo("repo-abc/" + name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def sec1() -> None:
    """--hook and --background ignore the current folder, even if it looks like a data repo."""
    CLAUDE.mkdir(parents=True)
    data_repo(EVIL, {"x/evil": {"kind": "cli", "approved": True, "status": "blocked", "blocked": "test",
                                "note": "", "spec": {"registry": "npm", "package": "evil", "version": "1.0.0"}}})
    data_repo(DATA, {})
    for flag in ("--hook", "--background"):
        res = run_sync(flag)
        check(f"SEC-1 {flag}, no link: exits 0 and says nothing",
              res.returncode == 0 and not res.stdout.strip() and not res.stderr.strip())
        check(f"SEC-1 {flag}, no link: current folder untouched", not (EVIL / ".star-triage").exists())
    (CLAUDE / "star-triage.json").write_text(json.dumps({"data": str(DATA)}), encoding="utf-8")
    res = run_sync("--hook")
    check("SEC-1 --hook, linked: uses the linked data repo",
          res.returncode == 0 and (DATA / ".star-triage" / "hook.json").exists())
    check("SEC-1 --hook, linked: current folder still untouched", not (EVIL / ".star-triage").exists())


def sec6() -> None:
    """Skill names and tar members cannot leave the skills folder."""
    os.environ.update(STAR_TRIAGE_DATA=str(DATA), CLAUDE_CONFIG_DIR=str(CLAUDE),
                      HOME=str(TMP / "home"), USERPROFILE=str(TMP / "home"))
    sys.path.insert(0, str(ENGINE / "scripts"))
    import sync  # reads the environment above at import time

    check("SEC-6 skills folder is inside the temp dir", TMP in sync.SKILLS_DIR.resolve().parents)
    check("SEC-6 plain skill name accepted", sync.skill_target("good-skill_1") is not None)
    for bad in ("", "..", "../x", "a/b", "a\\b", "C:x", "Upper", "-x", "x" * 65, ".hidden", None, 7):
        check(f"SEC-6 skill name {bad!r} rejected", sync.skill_target(bad) is None)
    for bad in ("/abs/file", "r/a\\b", "r/../x", "r/C:x", "r/a/C:\\x"):
        check(f"SEC-6 tar member {bad!r} rejected", sync.unsafe_member(bad))
    check("SEC-6 normal tar member accepted", not sync.unsafe_member("repo-abc/skills/good/SKILL.md"))

    skill_md = b"---\nname: x\n---\n"
    data = tarball({
        "skills/good/SKILL.md": skill_md,
        "skills/good/lib/helper.txt": b"ok",
        "skills/slash/SKILL.md": skill_md,
        "skills/slash/..\\..\\escape.txt": b"bad",
        "skills/drive/SKILL.md": skill_md,
        "skills/drive/C:escape.txt": b"bad",
        "skills/dots/SKILL.md": skill_md,
        "skills/dots/../../../escape.txt": b"bad",
    })
    skills = [{"name": n, "path": f"skills/{n}"} for n in ("good", "slash", "drive", "dots")]
    item = {"repo": "o/r", "commit": "a" * 40}
    worker = sync.Sync(sync.Runner(False, False), None, "user")
    installed = worker._unpack_skills(data, item, skills, set())
    check("SEC-6 only the clean skill is installed", installed == ["good"])
    check("SEC-6 clean skill files are in place",
          (sync.SKILLS_DIR / "good" / "SKILL.md").is_file() and (sync.SKILLS_DIR / "good" / "lib" / "helper.txt").is_file())
    check("SEC-6 skills folder holds nothing else", sorted(p.name for p in sync.SKILLS_DIR.iterdir()) == ["good"])
    check("SEC-6 no escaped file anywhere", not [p for p in TMP.rglob("*") if "escape" in p.name])

    for label, payload in (("not a tarball", b"this is not a tarball"), ("cut-off tarball", data[:40])):
        try:
            worker._unpack_skills(payload, item, skills[:1], {"good"})
            check(f"SEC-6 {label} is refused", False)
        except (tarfile.TarError, OSError, EOFError):
            check(f"SEC-6 {label} is refused", True)
    sync.MAX_TAR_MEMBERS = 3
    try:
        worker._unpack_skills(data, item, skills[:1], {"good"})
        check("SEC-6 too many tar members is refused", False)
    except tarfile.TarError:
        check("SEC-6 too many tar members is refused", True)


try:
    sec1()
    sec6()
finally:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
