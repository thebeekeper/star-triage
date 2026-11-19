# Changelog

## 5.7.0
One command for "I just starred something, review it and install it now".
- **`now` / `/star-triage:now`** (also "check my stars now", "install my new stars now"). Starts
  the star-triage workflow on GitHub (`--max N`, `--retriage owner/name,...` pass through), finds
  that run, and waits for it with a progress line every ~20 s (gives up after 20 minutes with the
  run URL). A run already going is waited for instead of starting a second one. Then:
  - failed run: its URL and GitHub's reason, and it stops;
  - no new stars: says so, and still installs what was approved before;
  - review PR merged itself: goes on;
  - review PR waits for you: shows what merging approves and asks "Merge it now? [y/N]" (the
    skill asks with AskUserQuestion). It never merges without a yes, and only the commit you saw.
  Then it pulls and installs what is approved through the same code as `sync --now`: only the
  safety delay is skipped; the daily limit, pause, and the High-risk and blocked rules stay. In
  manual mode it says what installs and lists the notes waiting for a tick. One summary at the
  end: repos reviewed, installed, waiting for you and why, and the restart line when needed.
  The review itself still runs on GitHub (scan, then Claude).
- Stops with one fix when gh is missing or not logged in, or the workflow isn't registered (UT-9).
- `sync.install_now` can hand its result to a caller (used by `now`); `sync --now` is unchanged.
- Tests: `tests/test_now.py` (offline; `fake_gh.py` now fakes workflow runs and review PRs).

## 5.6.0
Last round before launch: you choose the schedule, and setup says exactly what happens next.
- **You choose both timers.** Setup asks "How often should GitHub check for new stars?" (every
  1, 3 (recommended), 6, 12 hours or once a day) and, in the auto modes, "How long should a
  reviewed tool wait before it installs by itself?" (no wait, 1 hour (recommended), 6 or 24
  hours), in the terminal and in `/star-triage:setup` (flags `--check-every HOURS`,
  `--install-wait HOURS`). The interval is written into the workflow's `cron` line (minute 17)
  and pushed; config.yml keeps it as `check_every_hours`, and the doctor warns when the two
  disagree. Later: `python star-triage.py schedule` (shows both and asks), `schedule
  --check-every 1`, `schedule --install-wait 0`, or `/star-triage:schedule` in Claude Code. It
  commits and pushes the change. High-risk and blocked items still never install by themselves.
- **Both timers in one place (UT-19).** The README ("The two timers"), setup's plan and its
  final message give both numbers, where each is set, and the command to change them.
- **Why the first run is small (UT-21):** 3 repos, "so you see results in a few minutes"; after
  that `max_per_run` per check. In the plan, the final message and the README.
- **The merge rule, stated (UT-22).** Setup's last message and the skill say what the run's pull
  request does in the chosen mode, instead of "may be up to you".
- **`list`, `approve` and `unapprove` pull first (UT-23)**, like sync. If the pull fails they use
  the local notes and say "Couldn't fetch the latest reviews". The Kind column uses one style.
- **`token` names the account (UT-20).** It prints the GitHub account and the repo the secret
  goes to, and asks "Right account? [Y/n]" before `claude setup-token`.
- **Start-up line groups items that share a time:** "ponytail and brag install after 18:58".
- **README (UT-24 and launch).** A Quick start box under the intro (three Claude Code commands,
  about 10 minutes, one terminal line for the token). Daily use leads with the Claude Code
  commands, labels the rest "In a terminal, in your repo folder", and says how to check an
  install (`claude plugin list`, `claude mcp list`, restart Claude Code).
- **Fix: PyPI tools whose command differs from the package name.** A setup step like
  `graphify install` for the package `graphifyy` was rejected ("not the tool's own command"),
  although the triage prompt uses it as its example. The resolver now reads the package's
  commands from its wheel on PyPI (`entry_points.txt`, sha256-checked against PyPI), so code,
  not Claude, still decides what may run. `tests/test_install_plans.py` passes again.
- The doctor and setup offer to update an old `star-triage.py` launcher that lacks `schedule`.

## 5.5.1
- README rewritten for a new user (UT-1, UT-2, UT-17): what it does, six install steps with
  "You should see" and "If it fails" for each, what happens next with one table of the
  star-triage modes, daily use, updating, and reference tables. Each command has its own code
  block, with Windows and Mac/Linux side by side. The security model, cost, files, setup by
  hand, v4 upgrade and FAQ moved to the new `docs/HOW-IT-WORKS.md`.
- The setup skill and the data repo's README use the same steps and wording.
- Troubleshooting: "not a valid GitHub owner/repo shorthand", which scope to pick when
  installing the plugin, replacing a shared token with the `token` command, and "Workflow run
  was successfully requested" with no run listed yet.

## 5.5.0
Less to do for the user, in setup and every day.
- **Setup from the chat.** `/star-triage:setup` asks four questions (star-triage mode, repos per
  run, which data repo, Obsidian or not), shows setup's plan once, and runs `setup.py` itself
  with the new flags `--repo`/`--new-repo` (`--folder`), `--mode`, `--max`, `--obsidian`/
  `--no-obsidian`, `--no-auto-update`. Without `--yes` they only print the plan. `--repo` clones
  an existing data repo that isn't on this computer yet. The terminal flow is unchanged.
- **The token gets its own command:** `python "$HOME/.claude/star-triage.py" token`
  (`--api-key` for an API key). It is the only terminal step, with the same warnings and
  clear-screen offer as before, and it refuses to run unless stdin and stdout are a real
  terminal. The skill then checks the secret by name and starts the first run. If `gh` is
  missing or logged out, the skill says the one command to fix it and stops.
- **auto-plus is recommended** in setup (listed first, one sentence on what it does). Enter
  still keeps the current mode, and a config without `mode:` is still manual.
- **New configs wait 1 hour** before an automatic install (`auto_wait_hours: 1`, was 6).
  Existing configs keep their value; a config without the line still waits 6 hours.
- **Install now:** `python star-triage.py sync --now` or `/star-triage:sync now` installs what
  is approved without the safety delay. The daily limit, pause, and the High-risk and blocked
  rules stay; it is recorded in the history, so `undo` covers it. When something waits, the
  start line ends with "(install now: /star-triage:sync now)".
- **Reviews without Obsidian:** `python star-triage.py list` (`--waiting`, `--repo owner/name`)
  and `/star-triage:list` show a table of reviews with their install status and first "When
  you'd use it" example. `approve owner/name` and `unapprove owner/name` make the same edit
  as the Obsidian checkbox. Setup asks whether you use Obsidian (`obsidian:` in config.yml)
  and skips the Obsidian tips if not.
- **The plugin updates itself.** When your reviews come from a newer star-triage, the start-up
  hook runs `claude plugin marketplace update star-triage` and `claude plugin update
  star-triage@star-triage` in the background, at most once a day, and says to restart Claude
  Code. Setup also turns on Claude Code's own auto-update for the star-triage marketplace
  (the documented `autoUpdate` field in `~/.claude/settings.json`, with a backup).
  `auto_update: false` in config.yml turns both off.
- The doctor's "Not set up yet" points to `/star-triage:setup`; its launcher check looks for the
  5.5 commands.

## 5.4.1
- Security (audit SEC-20): the GitHub token no longer sits in `.git/config` while Claude runs.
  claude-code-action used to write it into the origin URL, where Claude's Read tool could see
  it. The Claude step now sets `allowed_bots` to a name no bot has, which makes the action use
  a credential helper that reads the token from its environment (Claude has no shell). Side
  effect: Claude Code runs in its default permission mode, so only the listed tools are
  allowed. The restore step also warns if a token ever appears in a URL in `.git/config`.

## 5.4.0
Fixes from a new-user test (UT numbers refer to that test).
- No more questions about you (UT-6). Reviews judge each repo on general quality and safety,
  and every note gets a "When you'd use it" section with 2 or 3 concrete examples; new notes
  without it are rejected. `about_me` is gone from new configs and the "about_me is empty"
  warning is gone; an existing `about_me` still works as extra context. Projects stay optional.
- Setup asks its questions first, then shows ONE plan and asks "Go ahead? [Y/n]" once, then
  runs straight through (UT-8). `--step-by-step` asks before each step, as before.
- Each GitHub setting setup turns on prints what it is, why, and where it lives on GitHub
  (UT-7).
- Before using an existing data repo, setup shows the repo, folder and GitHub account and asks
  (default no), and warns when `github_user` would change whose stars are read (UT-5).
- Warnings before and after `claude setup-token` (it prints the token), and an offer to clear
  the screen (UT-10).
- New data repos start on `main`; nothing assumes a branch name (UT-11).
- After the first push, setup waits for GitHub Actions to register the workflow, pushes a
  one-character change to the workflow file if it doesn't, and only then starts the first
  run. On failure it prints the Actions link and "click Run workflow", not an HTTP 404. New
  `setup --repair`; the doctor runs the same check and suggests it (UT-9).
- The doctor before setup says "Not set up yet" with one next step and exits 0 (UT-3).
- A stable setup command: the plugin keeps a launcher at `~/.claude/star-triage.py`, so
  `python "$HOME/.claude/star-triage.py" setup` works from any folder and after updates (UT-4).
- The restore step after Claude also names settings whose value changed (per-setting hashes,
  names only), e.g. `remote.origin.url` (UT-12). Open: claude-code-action puts the workflow
  token in the origin URL while Claude runs; a deny rule for `.git/` is not shipped because it
  can't be verified offline (audit SEC-20).
- actions/checkout v7.0.1 and actions/setup-python v7.1.0 (Node 24), still SHA-pinned (UT-13).
- Runs write `engine_version` into installs.json; the doctor and the Claude Code start message
  say when the plugin on this computer is older, with the update commands (UT-16).
- The start message names what waits and until when, in local time, e.g. `star-triage
  auto-plus: ponytail installs after 11:52 (6 h safety delay)`; items over the daily limit are
  named too. Messages say "star-triage mode" (UT-18).

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
