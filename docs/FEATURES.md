# Features and design choices

What star-triage does and, just as important, the design decisions behind it. Each
section is written so it can be used as-is in a launch post or README.

## 1. Star it and forget it

- **Picks up new stars within hours.** A lightweight check runs every 3 hours. GitHub has no
  event for "a user starred someone else's repo", so polling is the only option, and a check
  with nothing new finishes in under a minute without starting Claude.
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

- **Personal relevance.** You describe yourself and your projects once in `config.yml`; every
  verdict says which of your projects a repo matters for, or that it doesn't.
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
- **Your token stays out of reach.** The GitHub token is not stored in the checkout while
  Claude runs, only attached afterwards to push the review branch.
- **A validator gates every run.** Only new notes for that run's repos, with valid labels and a
  risk at or above the scan's floor, are accepted. Anything else fails the run.

## 5. Installs everything it can, by itself

- **Not just plugins.** Claude Code plugins, skills, MCP servers (hosted or run from npm or
  PyPI) and command-line tools from npm or PyPI, including the one-time setup step a tool's
  README tells you to run (like `graphify install`) and the optional extras a Python tool needs.
- **Installs while you work.** When Claude Code starts, a 0.1-second check pulls your approvals
  and installs anything new in the background. Next time, it tells you what it installed.
  Skills work straight away; plugins and MCP servers load on the next start. No command to
  remember, no scheduler to set up, the same on Windows, macOS and Linux.
- **Approve with a merge or a checkbox.** A run that approves installs opens a pull request and
  waits for you. Or tick `approve` on any note in Obsidian to install it, untick to remove it.
  Fully hands-off is one setting (`auto_merge: low-risk`): Low-risk installs approve themselves.
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

- **Install a plugin, run one skill.** `/plugin marketplace add thebeekeper/star-triage`,
  `/plugin install star-triage@star-triage`, then `/star-triage:setup`. Setup creates your private notes repo,
  asks about you and your projects, flips the one GitHub setting the workflow needs, stores your
  Claude token as a secret and starts the first run, asking before each change. Safe to run again.
- **Updates without copying code.** The engine is a GitHub Action (`@v1`) plus a Claude Code
  plugin. Your repo holds only your data and a 20-line workflow, so Action fixes reach your
  workflow by themselves, the plugin updates with one command, and a new user's repo isn't a
  wall of scripts. The doctor says when a newer plugin version is out.
- **Setup can be resumed.** If the first commit or push is declined or fails, running setup
  again commits the starting files and pushes them. Push errors come with the command that
  fixes them (`gh auth refresh -s workflow`, `gh auth setup-git`, `git pull --rebase`).
- **No example profile to forget.** A new `config.yml` has an empty `about_me` and only the
  `General` context; the doctor warns until `about_me` is filled in.
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
- **A doctor for every problem.** `python3 star-triage.py doctor` checks Python, git, your
  config, your repo layout, the secret, the Actions setting, the last run and the tools installs
  need, and prints the exact fix for each problem.
- **Errors in plain English.** Every run checks the config and secrets first. A broken setting,
  a missing secret or a missing permission shows up as one line on the run page with a link to
  the setting to change, instead of a stack trace.
- **One-command upgrade from v4.** `setup.py --migrate` swaps the copied code for the Action and
  plugin and keeps every note, setting and approval.
- **Works with or without the GitHub CLI.** Without `gh`, setup prints the links to click.
- **Integrity check after the review.** The run hashes the trusted scan copy and `.git/config`
  before Claude starts and fails without publishing if either changed.
- **One bad repo or note doesn't stop a run.** A repo the scanner can't read is rated High
  and can't be installed. A note that fails the checks is left out with a warning and the rest
  are published. If Claude stops early, the notes it finished are still published and the run
  is marked failed.
