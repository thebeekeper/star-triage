#!/usr/bin/env python3
"""End-to-end check of the CI pipeline's install side, on real repos and real registries.

Clones a few real repos (cached in tests/.cache), writes notes and install proposals the way
Claude would, then runs scan_repo, apply_scan, validate_notes and queue_installs from this
engine against a throwaway data repo. Checks every repo ends up with the expected install kind
and status, including look-alike packages and fake URLs that must be rejected.
Needs network (GitHub, npm, PyPI). Run: python tests/test_install_plans.py
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
ENGINE = Path(__file__).resolve().parent.parent
CACHE = ENGINE / "tests" / ".cache"
W = Path(tempfile.mkdtemp()) / "data"
W.mkdir(parents=True); shutil.copy(ENGINE/"template/config.yml", W); shutil.copy(ENGINE/"template/.gitignore", W)
(W/"tracker").mkdir(); (W/".triage/proposals").mkdir(parents=True); (W/".triage/repos").mkdir()
subprocess.run(["git","init","-q"],cwd=W); subprocess.run(["git","add","-A"],cwd=W); subprocess.run(["git","-c","user.name=t","-c","user.email=t@t","commit","-qm","i"],cwd=W)
R = CACHE
CACHE.mkdir(parents=True, exist_ok=True)
cases = [  # repo, clone dir, verdict, risk, proposal
 ("upstash/context7", "upstash_context7", "Install", "Low", {"kind":"mcp","name":"context7","url":"https://mcp.context7.com/mcp"}),
 ("ccusage/ccusage", "ccusage_ccusage", "Install", "Low", {"kind":"cli","registry":"npm","package":"ccusage"}),
 ("Graphify-Labs/graphify", "Graphify-Labs_graphify", "Install", "Medium", {"kind":"cli","registry":"pypi","package":"graphifyy","setup":["graphify","install"]}),
 ("evil/setup", "Graphify-Labs_graphify", "Install", "Low", {"kind":"cli","registry":"pypi","package":"graphifyy","setup":["curl","x"]}),
 ("latent-spaces/brag", "latent-spaces_brag", "Install", "Low", {"kind":"plugin"}),
 ("D4Vinci/Scrapling", "D4Vinci_Scrapling", "Install", "Medium", {"kind":"mcp","name":"scrapling","registry":"pypi","package":"scrapling","extras":["ai"],"command":"scrapling","args":["mcp"]}),
 # bad proposals, reusing clones under fake names
 ("evil/typosquat", "upstash_context7", "Install", "Low", {"kind":"mcp","name":"ctx","registry":"npm","package":"@upstash/context7-mcp"}),
 ("evil/badurl", "upstash_context7", "Install", "Low", {"kind":"mcp","name":"x","url":"https://evil.example.net/mcp"}),
 ("evil/inject", "upstash_context7", "Install", "Low", {"kind":"mcp","name":"x","registry":"npm","package":"@upstash/context7-mcp","args":["--x;rm -rf ~"]}),
]
batch=[]
for repo, d, verdict, risk, prop in cases:
    stem = repo.replace("/","__")
    if not (R/d).exists():
        subprocess.run(["git","clone","-q","--depth","1",f"https://github.com/{repo if not repo.startswith('evil/') else ''}".rstrip("/"), str(R/d)], check=False)
    sha = subprocess.run(["git","rev-parse","HEAD"],cwd=R/d,capture_output=True,text=True).stdout.strip()
    shutil.copytree(R/d, W/".triage/repos"/stem, ignore=shutil.ignore_patterns(".git"))
    item = {"repo":repo,"url":f"https://github.com/{repo}","id":None,"commit":sha,"cloned":True,"license":"MIT",
            "note_path":f"tracker/{stem}.md","local_path":f".triage/repos/{stem}","proposal_path":f".triage/proposals/{stem}.json"}
    batch.append(item)
    (W/item["proposal_path"]).write_text(json.dumps(prop))
    (W/item["note_path"]).write_text(f'''---
type: "Other"
category: ["Other"]
status: "Inbox"
verdict: "{verdict}"
risk: "{risk}"
saves_tokens: "No"
relevant_to: ["General"]
repo: "https://github.com/{repo}"
summary: "test"
install: "x"
licence: "MIT"
source: "GitHub star"
added: 2026-11-15
reviewed: 2026-11-15
tags: ["star-triage"]
---

# {repo}

## What it is

x

## When you'd use it

- one
- two

## Verdict: {verdict}

y

## Install

    x
''')
(W/".triage/new.json").write_text(json.dumps(batch))
env = dict(os.environ, PYTHONPATH="", GITHUB_REPOSITORY_OWNER="thebeekeper", STAR_TRIAGE_DATA=str(W))
for cmd in (["scan_repo.py"], ["apply_scan.py"], ["validate_notes.py","--changed"], ["queue_installs.py"]):
    r = subprocess.run([sys.executable, str(ENGINE/"scripts"/cmd[0]), *cmd[1:]], cwd=tempfile.gettempdir(), capture_output=True, text=True, env=env)
    if r.returncode:
        print(f"FAIL {cmd[0]} exited {r.returncode}:\n{(r.stdout+r.stderr)[-2000:]}"); sys.exit(1)
    print(f"ok   {cmd[0]}")
want = {"upstash/context7": ("mcp", "queued"), "ccusage/ccusage": ("cli", "queued"),
        "Graphify-Labs/graphify": ("cli", "queued"), "latent-spaces/brag": ("plugin", "queued"),
        "D4Vinci/Scrapling": ("mcp", "queued")}
items = json.loads((W/"installs.json").read_text())["items"]
fail = 0
for repo, (kind, status) in want.items():
    got = (items.get(repo, {}).get("kind"), items.get(repo, {}).get("status"))
    ok = got == (kind, status)
    fail |= not ok
    print(f"{'ok  ' if ok else 'FAIL'} {repo}: {got[0]} / {got[1]}")
for bad in ("evil/typosquat", "evil/badurl", "evil/inject", "evil/setup"):
    ok = bad not in items
    fail |= not ok
    print(f"{'ok  ' if ok else 'FAIL'} {bad} rejected")
# A PyPI package's commands come from its wheel: graphifyy installs `graphify` (5.6.0).
os.environ["STAR_TRIAGE_DATA"] = str(W); sys.path.insert(0, str(ENGINE/"scripts"))
import queue_installs
ok = queue_installs.console_scripts("[console_scripts]\ngraphify = graphify.__main__:main\nbad/x = a:b\n"
                                    "[gui_scripts]\ngui = a:b\n") == ["graphify"]
fail |= not ok
print(f"{'ok  ' if ok else 'FAIL'} PyPI commands are read from [console_scripts] only")
shutil.rmtree(W.parent, ignore_errors=True)
sys.exit(fail)
