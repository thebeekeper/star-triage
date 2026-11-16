# Tests

Run from the repo root:

| Command | What it checks | Needs |
|---|---|---|
| `bash tests/test_publish.sh` | publish.py: every mode (manual, auto, auto-plus) and the older `auto_merge` names, the rolling review PR, the no-permission error, changed pins on approved items, merge tied to the pushed commit | git, Python |
| `python tests/test_approval_gates.py` | `approve` written by the reviewer never approves an install; setup commands outside the package's own names (`python3.12`, `bash.exe`, `/bin/sh`) are rejected. Also run at the end of `test_publish.sh` | Python (temp dir only) |
| `python tests/test_ci_resilience.py` | a malformed `package.json`, `.mcp.json` or `marketplace.json` does not crash the scan; a repo that cannot be scanned gets a High floor; one bad note is set aside and the good ones go on. Also run at the end of `test_publish.sh` | git, Python (temp dir only) |
| `python tests/test_sync_safety.py` | sync.py: the hook ignores the current folder; skill names and tar members cannot leave the skills folder | Python (temp dir only) |
| `python tests/test_auto_mode.py` | auto mode: one mapping from `mode`/`auto_merge`; the background sync respects the safety delay, the daily limit and pause, and never installs High-risk or blocked items; a downgrade is removed and logged; `undo` removes the last batch only; the start message; `mode` edits, commits and pushes config.yml. Also run at the end of `test_publish.sh` | git, Python (temp dir only, installers replaced by a stand-in) |
| `python tests/test_setup.py` | setup.py and doctor.py (user test UT-3 to UT-16): "Not set up yet" exits 0; the stable launcher in `~/.claude`; an existing repo is confirmed with the GitHub account first; one plan, then no questions; every GitHub setting says what, why and where; new repos start on `main`; setup waits for GitHub to register the workflow and pushes a change to the workflow file if it doesn't; no raw HTTP 404; doctor's `--repair` hint; the plugin-older-than-reviews warning. Also run at the end of `test_publish.sh` | git, Python (temp dir, `fake_gh.py`, local bare "origin") |
| `python tests/test_action_steps.py` | action.yml's seal and `.git/config` restore steps, run as written in bash: added, removed and changed settings are named (never values), the clean config is put back, tampering still fails. Also run at the end of `test_publish.sh` | git, bash, Python |
| `python tests/test_install_plans.py` | scan → apply → validate → queue_installs on real repos; every install kind; look-alike packages, fake URLs, unsafe args and setup commands rejected | network (GitHub, npm, PyPI) |
| `claude plugin validate .` | the plugin and marketplace manifests | Claude Code |
| `actionlint template/.github/workflows/star-triage.yml` | the data repo's workflow | actionlint |

`fake_gh.py` stands in for the GitHub CLI in `test_publish.sh` and `test_setup.py`. Real repos for
`test_install_plans.py` are cloned once into `tests/.cache/` (git-ignored).

Not automated yet: sync.py installing for real (tested by hand with an isolated `HOME`,
`NPM_CONFIG_PREFIX`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`, see docs/HANDOVER.md), setup.py's
`gh repo create` and `claude setup-token` steps, and the Claude step of action.yml.
