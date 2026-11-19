# Features and design choices

What star-triage does and, just as important, the design decisions behind it. Each
section is written so it can be used as-is in a launch post or README.

## 1. Star it and forget it

- **Picks up new stars within hours.** A lightweight check runs every 3 hours by default.
  GitHub has no event for "a user starred someone else's repo", so polling is the only option,
  and a check with nothing new finishes in under a minute without starting Claude.
- **You choose both timers (5.6.0).** Setup asks how often GitHub should check (every 1, 3, 6,
  12 or 24 hours) and, in the auto modes, how long a reviewed tool waits before it installs by
  itself (no wait, 1, 6 or 24 hours). `schedule` (or `/star-triage:schedule`) shows and changes
  both later. The interval lives where GitHub reads it, the workflow's `cron` line (always at
  minute 17, away from the busy top of the hour); config.yml keeps a copy as `check_every_hours`
  so the doctor can warn when someone edits one and not the other. A wait of 0 is allowed: it is
  the user's explicit choice, and High-risk and blocked items still never install by themselves.
- **Both timers in one place (5.6.0).** The README, setup's plan and its final message name
  both numbers side by side (how often reviews run, how long installs wait), where each is set,
  and the command to change them.
- **Notes just appear.** A run that approves no new installs merges itself, so new notes show
  up in your tracker without a click. Only a run that wants to install something asks you.
- **Never a backlog, never a pile of PRs.** While a review PR is open, later runs add their notes
  to that same PR instead of opening new ones or waiting. Leave it for a week and you get one
  bigger PR, with nothing reviewed twice.
- **Only new repos, never twice.** A repo that already has a note is skipped, matched by URL.
  Existing notes are never edited by later runs.
- **Clears a backlog gradually.** `max_per_run` caps the work per run (newest stars first), so a
  pile of 300 old stars doesn't land in one giant run.

## 2. Verdicts written for you, not for everyone

- **No profile to fill in (5.4.0).** Verdicts are judged on general quality, safety and
  usefulness for most Claude Code users. Setup doesn't ask about you; an older `about_me` still
  works as extra context.
- **"When you'd use it", on every note (5.4.0).** 2 or 3 concrete situations where the tool
  helps, so you judge relevance yourself by reading them and ticking `approve`. The validator
  rejects a new note without at least two examples. Trade-off: in auto modes, installs are
  "generally good and safe" rather than tailored to you; the `auto_wait_hours` veto window
  still applies.
- **Projects are optional.** Name your projects in `contexts` and each note says which ones a
  repo matters for (`relevant_to`); leave just `General` and nothing is lost.
- **A hype detector.** Headline claims ("70% fewer tokens", "10x faster") are checked against
  independent reviews and labelled as measured, modelled or marketing.
- **Clear labels.** Each note gets a type, category, verdict (Install, Try, When needed,
  Optional, Portfolio only, Not now, Skip), risk, whether it saves tokens, and its licence,
  including unusual clauses such as Commons Clause.

## 3. Security in two layers

- **A scanner that can't be argued with.** Before any AI sees a repo, plain code scans it for
  hooks, MCP servers, install scripts, downloads piped into a shell, writes to Claude or agent
  config, permission bypasses, obfuscated code, compiled binaries, prompt-injection text and
  known-vulnerable dependencies (OSV-Scanner, pinned and checksum-verified). A malicious README
  can talk to an AI; it can't talk to a regex.
- **A risk floor the AI can raise but never lower.** The scan sets a minimum risk. If Claude
  rates a repo lower, the pipeline raises it back automatically and says so in the note.
- **AI review on top.** Claude reads the actual code behind each finding and explains whether
  it is real or a false positive (for example a pattern inside a test).
- **Fewer false alarms.** Findings in tests, examples, fixtures and benchmark harnesses are
  listed for information but don't raise the risk, because that code doesn't ship.
- **Every note shows its evidence.** An automated "Security scan" section lists each finding
  with file and line, plus the exact commit that was reviewed.

## 4. Built for hostile input

- **Nothing from a starred repo ever runs.** Repos are shallow-cloned with hooks, submodules
  and LFS disabled, symlinks stored as plain files, `.git` removed and large files dropped.
- **Claude has no shell.** It can only read, search, browse and write notes. `Bash` is blocked.
- **Claude's workspace can't change decisions.** The scan results are copied somewhere Claude
  can't reach before it starts, and every decision afterwards is made from that copy. The code
  that runs after Claude isn't in your repo at all, and anything Claude changes outside
  `tracker/` (config, installs.json, the plugin list) is reverted before the next step runs.
- **Your token stays out of Claude's reach.** Our checkout doesn't store the GitHub token
  (`persist-credentials: false`), and the Claude step is set up so claude-code-action gives git
  a credential helper instead of writing the token into `.git/config` (5.4.1). Claude has no
  shell, so it can't read the helper's environment either. Afterwards star-triage puts back a
  clean `.git/config`, logs which settings changed (names only), and warns if a token ever
  shows up in a URL there.
- **A validator gates every run.** Only new notes for that run's repos, with valid labels and a
  risk at or above the scan's floor, are accepted. Anything else fails the run.

## 5. Installs everything it can, by itself

- **Not just plugins.** Claude Code plugins, skills, MCP servers (hosted or run from npm or
  PyPI) and command-line tools from npm or PyPI, including the one-time setup step a tool's
  README tells you to run (like `graphify install`) and the optional extras a Python tool needs.
- **A tool's own commands come from the package, not from Claude (5.6.0).** A setup step must
  start with a command the package itself installs. For npm that is `bin`; for PyPI it is read
  from the release's wheel (`entry_points.txt`), downloaded from PyPI's file host and checked
  against PyPI's sha256, so `graphifyy` may run `graphify install` but never `curl`.
- **Installs while you work.** When Claude Code starts, a 0.1-second check pulls your approvals
  and installs anything new in the background. Next time, it tells you what it installed.
  Skills work straight away; plugins and MCP servers load on the next start. No command to
  remember, no scheduler to set up, the same on Windows, macOS and Linux.
- **Approve with a merge or a checkbox.** A run that approves installs opens a pull request and
  waits for you. Or tick `approve` on any note in Obsidian to install it, untick to remove it.
  Fully hands-off is one setting (`mode: auto`): Low-risk installs approve themselves.
- **Claude proposes, code decides.** Claude only suggests how to install a repo; plain code
  checks every proposal. A package must link back to that exact repo on npm or PyPI (renamed
  and transferred repos are matched by GitHub ID, so typosquats and look-alikes are rejected).
  A hosted MCP URL must appear in the repo itself. A setup command must appear word for word in
  the README and can't be a shell, downloader or interpreter.
- **Pinned to what was reviewed.** Plugins and skills install from the exact reviewed commit;
  packages from the exact version checked at review time. Upstream changes need a new review.
  (Anthropic's own community marketplace pins plugins the same way.)
- **Blocked means blocked.** High-severity scan findings or bundled binaries block an item: it
  is never installed in the background, and installing it by hand means typing `install`.
- **Your API keys never move.** An MCP server that needs a key gets the exact command to add it
  with your key, instead of anything reading your environment.
- **Brings old notes in.** Hand-written notes can be re-reviewed (`retriage`) so they get a scan
  and an install plan, keeping your status and approvals.
- **One command to check, install or undo.** `python3 star-triage.py sync` (`--status`, `--dry-run`,
  `--uninstall NAME`), or `/star-triage:sync` in Claude Code. Never passes `--yes`; every action
  is logged on your computer.

## 6. Yours, end to end

- **Runs in your own GitHub.** No server, no account, no third-party service. Your copy can be
  private.
- **Your Claude, your bill.** Uses your Claude subscription token or your own API key. Sonnet by
  default to keep usage low; empty checks use no Claude usage at all.
- **Free on GitHub's side.** Unlimited for public repos; about 240 Actions minutes a month for a
  private one, well inside the free 2,000.
- **Obsidian-native notes.** Plain Markdown with properties, so `tracker/` opens as an Obsidian
  vault with ready-made Bases views (Inbox, All repos, Try next, Installs, Cards). It
  reads fine on GitHub too.
- **Works on every OS.** No Windows Task Scheduler, cron or launchd needed; the optional sync is
  one cross-platform command.
- **Open and readable.** A handful of short Python scripts and one workflow file, MIT licensed.

## 7. Easy to set up, easy to fix, updates itself

- **Launch-ready README (5.6.0).** A Quick start box right under the intro: the three Claude
  Code commands, about 10 minutes, one terminal line for the token. The detailed Install
  section follows. Daily use leads with the Claude Code commands (they work from any folder)
  and labels the rest "In a terminal, in your repo folder".
- **The first run is small on purpose, and says why (5.6.0).** It reviews 3 repos so you see
  results in a few minutes; after that `max_per_run` per check.
- **The token command names the account first (5.6.0).** `token` prints the GitHub account and
  the repo the secret goes to, and asks "Right account? [Y/n]" before `claude setup-token`.

- **Install a plugin, run one skill.** `/plugin marketplace add thebeekeper/star-triage`,
  `/plugin install star-triage@star-triage`, then `/star-triage:setup`. Setup creates your private notes repo,
  flips the GitHub settings the workflow needs, stores your Claude token as a secret and starts
  the first run. Safe to run again.
- **One plan, one yes (5.4.0).** Setup asks its few questions (repo name, optional projects, the
  star-triage mode, subscription or API key), then shows everything it will do in one list and
  asks "Go ahead? [Y/n]" once. Then it runs straight through. `--step-by-step` asks before
  each step instead.
- **Setup inside Claude Code (5.5.0).** `/star-triage:setup` asks four questions in the chat
  (star-triage mode, repos per run, which data repo, Obsidian or not), shows setup's plan once,
  asks once, and runs `setup.py` itself with flags (`--repo`/`--new-repo`, `--mode`, `--max`,
  `--obsidian`/`--no-obsidian`). Without `--yes` the flags only print the plan, so nothing on
  GitHub changes before you say yes. The terminal flow still works for people who run it by hand.
- **The token is the only terminal step (5.5.0).** `python "$HOME/.claude/star-triage.py" token`
  runs `claude setup-token` and the hidden `gh secret set` prompt, with the warnings and the
  clear-screen offer. It refuses to run unless both its input and output are a real terminal,
  so it can't run inside Claude or into a log. The skill then checks the secret by name only
  (`gh secret list`) and starts the first run.
- **Missing GitHub CLI: one command (5.5.0).** If `gh` isn't installed or logged in, the skill
  says the one command that fixes it and stops, instead of a half-finished setup.
- **Plugin updates itself (5.5.0).** When `installs.json` says your reviews come from a newer
  star-triage, the startup hook runs the two update commands in the background, at most once
  a day, and says "restart Claude Code to use it". `auto_update: false` turns it off. Never
  with `--yes`.
- **Claude Code's own auto-update, turned on the documented way (5.5.0).** Setup sets
  `autoUpdate` on the star-triage entry of `extraKnownMarketplaces` in `~/.claude/settings.json`,
  the key the Claude Code docs name as taking priority over the `/plugin` toggle. Only that
  field, only when the entry exists, with a backup. This is the one place star-triage edits a
  Claude Code settings file, because no CLI command or flag can do it.
- **Every GitHub setting explained (5.4.0).** For each setting it turns on, setup prints what it
  is, why star-triage needs it, and where it lives (*Settings > Actions > General > Workflow
  permissions*), so you can check or undo it later.
- **Confirms the repo and the account (5.4.0).** Before using an existing data repo, setup shows
  the repo, the folder and the GitHub account you're logged in as, and asks (default no). It
  warns when the repo belongs to another account, and when a change to `github_user` would make
  the repo read someone else's stars.
- **Warns around the token (5.4.0).** `claude setup-token` prints the token on screen, so setup
  says "don't share or screenshot this part" before it, and offers to clear the screen after.
- **New repos start on `main` (5.4.0),** whatever git's own default is, and nothing in
  star-triage assumes a branch name: the workflow uses the repo's default branch.
- **Waits for GitHub to see the workflow (5.4.0).** GitHub sometimes misses the workflow in a
  brand-new repo's first push. Setup polls until Actions lists it (up to a minute), pushes a
  one-character change to the workflow file if it doesn't, and only then starts the first run.
  If that still fails, it prints the Actions link and "click Run workflow", never a raw HTTP
  404. The doctor runs the same check and suggests `setup --repair`.
- **A setup command that doesn't change (5.4.0).** The plugin keeps a launcher at
  `~/.claude/star-triage.py`, so `python "$HOME/.claude/star-triage.py" setup` works from any
  folder and survives plugin updates (the plugin's own path has its version number in it).
- **"Not set up yet" is not an error (5.4.0).** Before setup, the doctor prints one next step
  and exits 0, instead of a red failure.
- **Updates without copying code.** The engine is a GitHub Action (`@v1`) plus a Claude Code
  plugin. Your repo holds only your data and a 20-line workflow, so Action fixes reach your
  workflow by themselves, the plugin updates with one command, and a new user's repo isn't a
  wall of scripts. The doctor says when a newer plugin version is out.
- **Says when this computer is behind (5.4.0).** Each run writes its engine version into
  `installs.json`. When the plugin on your computer is older, the doctor and the Claude Code
  start message say so, with the two update commands. The Action updates itself; the plugin
  waits for you, so the two could disagree about approvals without this.
- **Setup can be resumed.** If the first commit or push is declined or fails, running setup
  again commits the starting files and pushes them. Push errors come with the command that
  fixes them (`gh auth refresh -s workflow`, `gh auth setup-git`, `git pull --rebase`).
- **No example profile to forget.** A new `config.yml` has only the `General` context and no
  example project to replace.
- **Tells you about the backlog.** Setup and the README say that every existing star is
  reviewed, `max_per_run` per run every 3 hours, and what to do after the first run.
- **The startup hook finds Python.** It tries `python3`, then `python`, then `py`, so it runs
  on macOS and Linux as well as Windows.
- **The Actions it uses are pinned.** `action.yml` pins checkout, setup-python and
  claude-code-action to full commit SHAs, so a moved tag can't change what runs.
- **A way out.** The README has a "Stop or uninstall everything" section.
- **Your token never passes through an AI or a file.** It goes from Claude's login page straight
  into GitHub's hidden secret prompt.
- **Catches the commit-email trap.** Setup uses your private GitHub noreply address, and the
  check warns if your commit email points at someone else's account (an easy mistake that
  makes your commits show a stranger as the author).
- **A README you can follow step by step (5.5.1).** Six install steps, one command per code
  block, Windows and Mac/Linux side by side, and for each step what you should see and a
  folded "If it fails" with the fix. The why and the security detail live in
  `docs/HOW-IT-WORKS.md`, so the README stays about what to do.
- **A doctor for every problem.** `python3 star-triage.py doctor` checks Python, git, your
  config, your repo layout, the secret, the Actions setting, the last run and the tools installs
  need, and prints the exact fix for each problem.
- **Errors in plain English.** Every run checks the config and secrets first. A broken setting,
  a missing secret or a missing permission shows up as one line on the run page with a link to
  the setting to change, instead of a stack trace.
- **One-command upgrade from v4.** `setup.py --migrate` swaps the copied code for the Action and
  plugin and keeps every note, setting and approval.
- **Works with or without the GitHub CLI.** Without `gh`, setup prints the links to click.
- **Integrity check after the review.** The run hashes the trusted scan copy before Claude
  starts and fails without publishing if it changed. `.git/config` is saved before and put back
  after, because claude-code-action rewrites it itself.
- **One bad repo or note doesn't stop a run.** A repo the scanner can't read is rated High
  and can't be installed. A note that fails the checks is left out with a warning and the rest
  are published. If Claude stops early, the notes it finished are still published and the run
  is marked failed.

## 8. Auto mode

- **One setting, three plain choices.** `manual` (I approve every install, the default), `auto`
  (safe things install by themselves, Low risk only), `auto-plus` (Low and Medium). It replaces
  the older `auto_merge` key, which is still read: `notes` is manual, `low-risk` is auto, and
  `mode` wins when both are set. One function reads it for the workflow, setup, the doctor
  and the sync.
- **auto-plus is the recommended choice (5.5.0).** Setup lists it first, marked recommended,
  with one sentence on what it does. It is still a choice: Enter keeps the current mode, a
  config without `mode:` still means manual, and the skill never picks for you.
- **A 1-hour wait for new setups (5.5.0).** New configs get `auto_wait_hours: 1` (was 6).
  Existing configs keep what they say; a config with no such line keeps the old 6.
- **Install now (5.5.0).** `/star-triage:sync now` or `python3 star-triage.py sync --now`
  installs what is approved right away. It skips only the safety delay: the daily limit,
  pause, and the High-risk and blocked rules stay, and it is recorded in the history like an
  automatic install, so `undo` covers it. When something waits, the start line ends with
  "(install now: /star-triage:sync now)".
- **Review and install now, in one go (5.7.0).** `/star-triage:now` ("check my stars now",
  "install my new stars now") or `python3 star-triage.py now` starts the workflow on GitHub,
  waits for it with a progress line every ~20 s, and then installs through the same code as
  `sync --now`. The review is not skipped: the scan and Claude still run in the Action, and only
  the schedule and the safety delay are. Design choices:
  - A run already going (the workflow's concurrency group) is waited for, not doubled.
  - The started run is found by id (a workflow_dispatch run that wasn't listed before), not by
    the computer's clock, and the review PR by its update time on GitHub against the run's own
    start time. Only a PR on a `star-triage/` branch of this repo by github-actions counts.
  - A review PR that waits for you is shown with what merging approves, then asked about
    ("Merge it now? [y/N]" in a terminal, AskUserQuestion in the skill). Nothing merges on
    anything but a yes. The skill answers with `now --merge N --head SHA`, and the merge uses
    `--match-head-commit`, so a PR that changed after you saw it is not merged.
  - No new stars is not an error: it still installs what was approved before.
  - In manual mode it says plainly what installs (ticked items, and Install verdicts in a merged
    review PR) and lists the notes waiting for a tick, instead of installing nothing silently.
  - It ends with one summary: repos reviewed, what installed, what waits for you and why (High
    risk, blocked, not ticked, Skip, manual install, an open review PR), and the restart line
    when a plugin or MCP server was installed. It gives up after 20 minutes with the run URL.
- **Four ways to change it, one result.** `python3 star-triage.py mode` (a 1/2/3 menu),
  `mode auto`, the `/star-triage:mode` skill ("turn on auto mode"), and the same question in
  setup. Each one edits `config.yml`, commits "Switch star-triage to <mode>" and pushes; a
  failed push prints the commands to run.
- **Auto modes approve by risk, not only by verdict (5.3.0).** In `auto` and `auto-plus`,
  anything installable within the mode's risk limit is approved, whatever the verdict, unless
  the verdict is Skip or Not now. Before, only Install verdicts were ever approved, and a new
  user with no `about_me` got no Install verdicts, so nothing installed by itself (user test
  UT-14). `manual` keeps the old rule: merging approves Install verdicts only.
- **Never High, never blocked.** No mode merges a PR that approves a High-risk install, and
  the background sync never installs a High-risk or blocked item. A blocked item still needs
  a typed `install`.
- **A veto window.** In auto modes an item installs only once its review is `auto_wait_hours`
  old (1 h in new configs, 6 h in older ones). That leaves time to untick `approve` on the
  note from a phone, or to pause.
  Items that aren't due wait for a later Claude Code start.
- **A daily cap.** At most `auto_daily_limit` items (default 5) install per day on one
  computer, so a burst of stars or a bad day at a registry can't install a pile at once.
- **Pause.** `pause` puts a flag file in `.star-triage/`; nothing is installed or removed
  automatically on that computer until `resume`. It works in every mode and needs no network.
- **Undo.** `undo` removes everything the most recent automatic batch installed and unticks
  those notes so they stay removed. It asks y/n first.
- **History.** Every automatic install and removal is one line in `.star-triage/history.jsonl`
  with the date, the item and the reason. `history` shows the last 20. A removal caused by a
  later review is recorded as "removed: downgraded by re-review".
- **One line at startup.** In auto modes the Claude Code start message is a single line that
  names what waits and until when, in local time, for example
  `star-triage auto-plus: installed context7; ponytail installs after 11:52 (1 h safety delay) (install now: /star-triage:sync now)`.
  Items that share a time are named together: `ponytail and brag install after 18:58` (5.6.0).
- **The merge rule, said plainly (5.6.0).** Setup's last message states what the run's pull
  request will do in the chosen mode (manual: merges itself when it only adds notes; auto:
  unless it approves an install above Low risk; auto-plus: above Medium), instead of "may be up
  to you". The sentences live next to the code that decides it (`paths.MERGE_RULES`).
- **Called "star-triage mode".** Messages and docs say "star-triage mode", so it isn't confused
  with Claude Code's own auto mode (a permission mode).
- **Nobody is switched silently.** The default stays manual, an existing config keeps its
  meaning, and setup writes `mode` only when you pick a different one.

## 9. No Obsidian needed

- **One table in the terminal or the chat (5.5.0).** `python3 star-triage.py list` (or
  `/star-triage:list`) shows every review, newest first: repo, kind, verdict, risk, install
  status (installed, installs after HH:MM, waiting for you, blocked, nothing to install) and the
  first "When you'd use it" example. `--waiting` keeps only what needs you; `--repo owner/name`
  prints the summary, all examples and the security findings.
- **Approve from the command line (5.5.0).** `approve owner/name` and `unapprove owner/name`
  make the same one-line edit as the Obsidian checkbox, so every rule stays the same: a blocked
  item still needs `sync` in your own terminal and a typed `install`, and High-risk items never
  install by themselves in the auto modes. Claude can run them when you say "approve ponytail".
- **Always the latest reviews (5.6.0).** `list`, `approve` and `unapprove` do the same quiet
  `git pull --ff-only --autostash` as sync before reading, so a merged review shows at once. If
  the pull fails they use the local notes and say so. The Kind column uses one style.
- **Obsidian is optional (5.5.0).** Setup asks; with `obsidian: false` it skips the Obsidian
  tips and points to `list`, and sync's "waits for you" hints say `approve owner/name` instead
  of "tick approve in Obsidian".
