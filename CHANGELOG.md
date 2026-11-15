# Changelog

## 5.3.1
- Fix: re-reviewing a repo kept the old `approve: false` even when that was only the default
  the last review wrote, so a newly approved item stayed unticked and never installed. A
  re-review now keeps the old value only when you chose it (ticked something that wasn't
  approved, or unticked something that was).
- Sync's "waiting" list also shows approved items you unticked, and says so.

## 5.3.0
- Auto modes now install anything within their risk limit (`auto`: Low, `auto-plus`: Low and
  Medium), whatever the verdict, unless the review says Skip or Not now. Before, only Install
  verdicts were ever approved, so a new user often got nothing installed. `manual` is
  unchanged. High-risk and blocked items still never install by themselves.
- Sync no longer says "approve installs by merging a review PR" when the PR is already merged.
  It lists each item that waits, with its verdict and risk, and the note to tick.

## 5.2.1
- Fix: every run that reviewed new stars failed with "Integrity check failed". The check
  hashed `.git/config`, but the Claude step (claude-code-action) changes that file itself
  when it sets up git. `.git/config` is now saved before Claude and restored right after, and
  the run logs which settings were changed (names only). The tamper check still covers the
  trusted scan copy.

## 5.2.0
Auto mode. The default is still manual; nobody's mode changes unless they choose it.
- One setting, `mode`: `manual` (I approve every install), `auto` (safe things install by
  themselves, Low risk only) or `auto-plus` (Low and Medium). High-risk and blocked items are
  never installed automatically.
- Change it with `python star-triage.py mode` (menu), `mode auto`, the `/star-triage:mode`
  skill, or setup. Each one updates `config.yml`, commits and pushes.
- In auto modes an item installs only after a safety delay from its review (`auto_wait_hours`,
  default 6) and under a daily limit (`auto_daily_limit`, default 5).
- `pause` / `resume` stop or restart everything automatic on one computer. `undo` removes the
  most recent automatic batch. `history` lists the last 20 automatic installs and removals.
- The Claude Code start message in auto modes is one line; the doctor shows the mode and warns
  when paused.
- The older `auto_merge` key still works (`notes` = manual, `low-risk` = auto); `mode` wins.
- `installs.json` items carry `reviewed_at`; `star-triage.py` (the launcher) gained the new
  commands, and setup offers to update an older copy.

## 5.1.0
Fixes from a November 2026 security and robustness audit, in four batches.

Batch 1, setup and the startup hook can't act on the wrong folder:
- The startup hook installs only from the data repo you linked, never from the folder Claude
  Code was opened in.
- Setup stops when there is no terminal to answer its questions; no answer is never a yes.
- Setup refuses the engine folder and any repo that already has files, and commits only the
  template files it lists.

Batch 2, approval gates and install safety:
- An `approve` written by the reviewer is ignored; note ticks and changed pins count as
  approvals, so such a PR waits for you.
- Setup commands are limited to the package's own command names.
- Only a PR opened by github-actions[bot] from a branch in your own repo is added to or merged,
  and the merge is tied to the pushed commit.
- The trusted scan copy and `.git/config` are hash-checked after Claude runs; `id-token: write`
  removed.
- Sync refuses skill names and archive members that would leave the skills folder.

Batch 3, runs survive bad input:
- A repo that can't be scanned gets a High floor and a blocker instead of stopping the run.
- A bad note is set aside with a warning; the good notes are still published.
- Notes are matched by repository ID as well as URL, so renames and forks aren't reviewed twice.
- If Claude stops early, the notes it finished are published and the run is marked failed.
- Non-ASCII file names no longer break validate, publish or the scan.

Batch 4, setup, docs and going public:
- The startup hook tries `python3`, then `python`, then `py`. Docs say `python3` (`python` on
  Windows).
- A missing PyYAML gets the right install command, with apt, dnf and Homebrew hints.
- Setup step 3 commits and pushes the starting files if the first commit never happened, and
  explains push errors. The doctor checks the workflow is on GitHub and enabled, and warns
  about an old plugin version.
- Setup and the README say that all existing stars get reviewed, `max_per_run` per run every
  3 hours, and what to do after the first run.
- A new `config.yml` has an empty `about_me` and only the `General` context.
- One plugin-install instruction everywhere; full links to Troubleshooting; a "Stop or
  uninstall everything" section in the README.
- Third-party Actions in `action.yml` are pinned by commit SHA. `SECURITY.md` added.
- The default `max_per_run` is 8 everywhere (the pipeline's fallback was 10).

## 5.0.0
- The engine is now a GitHub Action (`thebeekeper/star-triage@v1`) plus a Claude Code plugin.
  Your repo keeps only your data, a launcher (`star-triage.py`) and a 20-line workflow.
- Automatic installs come from the plugin's own startup hook; no settings.json editing.
- Setup can create your private data repo, link an existing one, or migrate a v4 copy (`--migrate`).
- Skills renamed: `/star-triage:setup`, `/star-triage:sync`, `/star-triage:triage`.
- Tests folder (`tests/`).

## 4.1.0
- The plugin marketplace lists every reviewed plugin, so a blocked plugin you confirm can install.
- Verdicts are judged for you: clear wins get Install again; each verdict is defined.
- Troubleshooting: PowerShell execution policy for npm tools.

## 4.0.0
- Installs plugins, skills, MCP servers and CLI tools, each pinned and checked against its repo.
- Approve by merging or with the `approve` checkbox in Obsidian; automatic background installs.
- `auto_merge: low-risk`; `retriage` to re-review existing notes.

## 3.0.0
- Notes-only PRs merge themselves; later runs add to an open PR.
- Guided `setup.py`, `doctor.py`, and a setup check at the start of every run.

## 2.0.0
- Plain-code security scan with OSV, risk floor, trusted scan copy, validator.
- Approved plugins pinned in a personal marketplace; `sync.py`; checks every 3 hours.

## 1.0.0
- Reads your stars, Claude writes one Obsidian note per repo, opens a pull request.
