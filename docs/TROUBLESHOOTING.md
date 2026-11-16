# Troubleshooting

Problems people hit while setting up star-triage, with the fix for each.
Found a new one? Open an issue so it can be added here.

**First, run the check:** `python3 star-triage.py doctor` in your data repo (or `/star-triage:setup`
in Claude Code). It tests Python, git, your
config, the secret, the Actions setting and the last run, and prints the fix for anything wrong.
`python3 star-triage.py setup` fixes most of it for you and is safe to run again.

Commands on this page are written with `python3`. On Windows, type `python` instead.

## Setup script

### `python` or `python3` is not found

macOS and Linux usually have `python3` and no `python`; Windows usually has `python` and no
`python3`. Use the one your computer has.

Neither works on Windows: install Python from https://www.python.org/downloads/ and tick **Add
python.exe to PATH** in the installer, then open a new terminal. `py star-triage.py setup` also
works if the Python launcher is installed.

### `PyYAML is not installed`

`setup.py` and `doctor.py` need this small library on your computer (the GitHub workflow
installs its own). Run `python3 -m pip install --user pyyaml`.

If pip answers `error: externally-managed-environment`, your Python is managed by the system
(PEP 668) and refuses plain pip. Use the system's package instead:

- Debian, Ubuntu: `sudo apt install python3-yaml`
- Fedora: `sudo dnf install python3-pyyaml`
- Homebrew Python on macOS: `python3 -m pip install --user --break-system-packages pyyaml`
  (Homebrew has no PyYAML package; this installs it for your user only)

### Setup says the GitHub CLI is not available

On Windows the quickest install is `winget install --id GitHub.cli`; then open a **new**
terminal (the old one doesn't see it yet). Elsewhere, install it from https://cli.github.com.
Then run `gh auth login` (GitHub.com, HTTPS, log in with a web browser) and run
`python3 star-triage.py setup` again. Without it, setup prints the three links to do the GitHub
steps by hand.

### Setup stopped before the first commit or push, and running it again changes nothing

Fixed in 5.1.0: step 3 of setup now commits `config.yml` and any starting file that was never
committed (the workflow, the launcher, `tracker/`), and pushes with `git push -u origin HEAD`.
Update the plugin, then run `python3 star-triage.py setup` again. The doctor reports "The
workflow is not on GitHub yet" while the push is still missing.

### `Push failed` during setup

Setup prints git's error and the usual fix:

- `refusing to allow an OAuth App to create or update workflow ... without workflow scope`:
  run `gh auth refresh -s workflow`.
- `Authentication failed`, `could not read Username`, `Permission denied`: git has no GitHub
  login. Run `gh auth setup-git`.
- `non-fast-forward` or `fetch first`: GitHub has commits your folder does not. Run
  `git pull --rebase`.

Then run setup again; it picks up at the push.

### The first runs review far more repos than I starred today

Every repo you starred before setup counts as new. They are reviewed newest first,
`max_per_run` per run (default 8), at each check (every 3 hours unless you changed it), until
none are left. The first run takes only 3, so you see results in a few minutes. Lower `max_per_run` in
`config.yml` to go slower, list repos under `ignore` to leave them out, or disable the workflow
to pause (`gh workflow disable star-triage.yml`).

### Setup says it "needs a terminal you can type in"

Setup was started without a keyboard attached (from Claude Code, a script, or with input piped
in), so it stops instead of answering its own questions. Inside Claude Code, use
`/star-triage:setup`: it asks the questions in the chat and runs setup with flags
(`--repo`/`--new-repo`, `--mode`, `--max`, `--obsidian`/`--no-obsidian`, then `--yes`). Or open a
terminal window and run the same command there. `--check` still works anywhere.

### Setup says "This folder is the star-triage engine" or "already has files"

Setup only offers to turn the current folder into your data repo when it is a git repo with no
commits or no tracked files, and never the engine folder. Anywhere else it offers to create a
new private repo instead. To use an empty repo you already made, run setup from inside it and
answer `y` (the default is No). It commits only the template files it lists.

### The token prompt: where do I paste it?

`claude setup-token` opens your browser, then prints a long token in the terminal. Copy it.
Setup then runs `gh secret set`, which shows `? Paste your secret`: paste there and press Enter.
Nothing appears while you paste; that is normal. Clear the terminal afterwards
(`cls` on Windows, `clear` elsewhere).

### `/star-triage:setup` says "Not set up yet"

That is the normal state before setup has run, not an error. Since 5.5.0 the skill carries on
by itself: four questions, one plan, then setup runs from the chat. Only the token step is left
for a terminal. Before 5.4.0 this showed as a red "Exit code 1" and "[FAIL] No star-triage data
repo found".

### The setup command has a long path with a version number in it

Older versions gave a path like `.../plugins/cache/star-triage/star-triage/5.2.0/scripts/setup.py`,
which breaks after the next plugin update. Since 5.4.0 the plugin keeps a launcher at
`~/.claude/star-triage.py`, so this works from any folder, after any update:

```
python3 "$HOME/.claude/star-triage.py" setup
```

`"$HOME/..."` works in PowerShell, bash and zsh. In the old Windows Command Prompt (cmd), use
`python "%USERPROFILE%\.claude\star-triage.py" setup`. If the file isn't there yet, run
`/star-triage:setup` once (it writes it), or start Claude Code once.

### Setup asks "Set up <owner>/<repo> (logged in as <you>)?"

Setup found a data repo you set up before (in this folder, or the one it linked last time) and
wants to be sure before changing it. Answer `y` if that is the repo and the GitHub account you
mean. Answer `n` to be offered a new data repo instead. If the account is wrong, stop
(Ctrl+C), switch with `gh auth switch` (or `gh auth login`), and run setup again in the
terminal logged in to the right account (`gh auth status` shows which).

If setup says a change to `github_user` would make the repo read someone else's stars, answer
`n` unless that is what you want.

### I want setup to ask before each step

Run it with `--step-by-step`. Without it, setup asks its questions first, shows one plan, and
after "Go ahead?" runs straight through (only the token paste and "clear the screen?" come
after).

### Claude token was shared

`claude setup-token` prints the token on screen, so it ends up in screenshots and pasted
transcripts. Anyone with it can use your Claude subscription. Make a new one and replace the
secret. The easy way: run the token command again in your own terminal and answer `y` to
"Replace it?":

```
python "$HOME/.claude/star-triage.py" token      # Windows
python3 "$HOME/.claude/star-triage.py" token     # Mac and Linux
```

Or by hand:

```
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo <you>/<your-repo>
```

(paste the new token at the hidden prompt). Then remove the old one: if your Claude account
settings list active tokens or sessions, revoke it there. Clear the terminal afterwards
(`cls` on Windows, `clear` elsewhere). Setup offers to do that for you.

### My data repo's branch is `master`, not `main`

Nothing breaks: the workflow runs on your repo's default branch, whatever its name. Setup
5.4.0 and later starts new repos on `main`. To rename an old one: on GitHub, *Settings >
General > Default branch*, rename it to `main`; then in your data repo run
`git branch -m master main`, `git fetch origin`, `git branch -u origin/main main`.

### `/star-triage:setup` asks me to run a `token` command in a terminal

That is the one step setup can't do from the chat: `claude setup-token` prints your Claude
token, and the token must never pass through Claude. In PowerShell (Windows) or Terminal (Mac),
not in Claude Code and not with `!`, run:

```
python "$HOME/.claude/star-triage.py" token      # Windows
python3 "$HOME/.claude/star-triage.py" token     # Mac and Linux
```

It logs you in through the browser, shows the token, and you paste it at a hidden prompt that
sends it straight to GitHub. It offers to clear the screen afterwards. Using an Anthropic API
key instead: add `--api-key`. Then tell Claude you're done; the skill checks the secret by name
and starts the first run.

### `token` asks "Right account?"

Before anything else, `token` prints the GitHub account the GitHub CLI is logged in to and the
repo the secret goes to. Answer `n` if either is wrong: nothing is changed. Switch with
`gh auth switch` (or `gh auth login`), check with `gh auth status`, and run it again. A new
terminal window can be logged in to a different account than the one setup used (for example
when you test with a second account): check `gh auth status` there first. "Careful: o/r belongs
to X, not Y" means the repo's owner is not the logged-in account; only the owner (or an admin)
can store its secrets.

### `token` says it "only runs in your own terminal"

It was started from Claude Code, a script, or with its output redirected. It refuses so the
token can't end up in a chat or a log. Run it in a terminal window you type in.

### Setup printed a plan and "Nothing was changed"

Run with answer flags (`--repo`, `--new-repo`, `--mode`, `--max`, `--obsidian`), setup only shows
its plan. That's how `/star-triage:setup` shows it to you in the chat. The same command with
`--yes` carries it out. A message like `--mode must be one of` names a flag that was wrong.

## Installing Claude Code (needed once, for the token)

### `claude : The term 'claude' is not recognized` (Windows)

**Cause:** Claude Code isn't installed on this computer.

**Fix:** in PowerShell (no admin needed):

```powershell
irm https://claude.ai/install.ps1 | iex
```

On macOS or Linux: `curl -fsSL https://claude.ai/install.sh | bash`

### Installed successfully, but `claude` is still not recognized (Windows)

**Cause:** the installer put `claude.exe` in `%USERPROFILE%\.local\bin`, but that folder
isn't on your PATH. The installer prints a "Setup notes" warning about this.

**Fix:** add the folder to your user PATH (no admin needed):

```powershell
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path","User") + ";$env:USERPROFILE\.local\bin", "User")
```

Then **open a new PowerShell window**. A window that is already open keeps its old PATH, so
`claude` still fails there. To fix the current window without reopening it:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
claude --version
```

### I only need the token, not Claude Code itself

You still need the CLI once to run `claude setup-token`. If PATH is giving you trouble, call
it by its full path instead:

```powershell
& "$env:USERPROFILE\.local\bin\claude.exe" setup-token
```

Paste the token into **Settings → Secrets and variables → Actions → New repository secret**
as `CLAUDE_CODE_OAUTH_TOKEN`. Never paste it into an issue, chat or commit.

## Git and commits

### My commits show someone else's name or avatar, or no avatar at all

**Cause:** GitHub links a commit to an account by its email address. With a no-reply address
of the form `<number>+<username>@users.noreply.github.com`, the **number** decides the account,
not the username. A placeholder like `12345678+you@...` or `ID+you@...` either links to a
stranger who owns that ID or to nobody.

**Fix:** copy your real address from **GitHub → Settings → Emails** (under "Keep my email
addresses private"), then in your repo folder:

```powershell
git config user.email "<your real no-reply address>"
git commit --amend --reset-author --no-edit
git push --force
```

Check before pushing: `git log -1 --format="%ae"` should print your real address. Only
force-push like this on a repo nobody else has cloned yet.

### `nothing to commit, working tree clean` after copying files in (setting up by hand)

**Cause:** the `template/` files landed somewhere else (Downloads, or a subfolder named after the zip).

**Fix:** `dir` / `ls` inside the repo folder and check the files are really there, at the top
level and in `tracker/`, before `git add .`.

### The `.github` folder is missing on GitHub (setting up by hand)

**Cause:** folders starting with a dot are hidden by some file managers and zip tools, and get
left out of drag-and-drop uploads. Without `.github/workflows/`, the Actions tab has no
star-triage workflow. The doctor reports this as "The workflow is not on GitHub yet".

**Fix:** check the repo on GitHub shows `.github/workflows/star-triage.yml`. If not, add it
with **Add file → Create new file** and type the full path, including the folders, as the
file name.

## GitHub Actions

### The Actions tab shows no workflow, or a button to enable workflows

**Cause:** the workflow file was never pushed, or GitHub paused it. GitHub pauses scheduled
workflows after 60 days without activity in the repo.

**Fix:** run `python3 star-triage.py doctor`. If it says the workflow is not on GitHub, run setup
again (step 3 pushes it). If it says the workflow is disabled, run
`gh workflow enable star-triage.yml`, or click the enable button on the **Actions** tab.

### `workflow star-triage.yml not found on the default branch` (HTTP 404), or "GitHub Actions hasn't registered the workflow"

**Cause:** GitHub sometimes misses the workflow file in a brand-new repo's very first push
(the repo is seconds old). The file is there and valid, but **Actions** says "No workflows
found", and later pushes that don't touch the workflow file never make GitHub look again.

**Fix:** setup 5.4.0 and later handles this itself: it waits up to a minute, then pushes a
one-character change to the workflow file and waits again. If you still see it, run
`python3 star-triage.py setup --repair` in your data repo (same fix), or edit
`.github/workflows/star-triage.yml` on GitHub (any small change) and commit. Then open
**Actions > star-triage > Run workflow**. After clicking, GitHub shows "Workflow run was
successfully requested" while the list still says no runs: refresh after a few seconds.

### Warnings: `Node.js 20 is deprecated` or `ubuntu-latest label will migrate to Ubuntu 26`

Both are GitHub notices, not errors. 5.4.0 moved the pinned actions (checkout, setup-python)
to their Node 24 releases, so the Node.js warning goes away with it. The Ubuntu notice is
about GitHub's runner image; star-triage needs only bash, git, Python and curl, which every
Ubuntu image has. To pin the image anyway, change `runs-on: ubuntu-latest` to
`runs-on: ubuntu-24.04` in your repo's `.github/workflows/star-triage.yml`.

### `Restoring .git/config. Settings changed during the Claude step`

Expected on every run. claude-code-action sets a git user and an origin URL with its token
before Claude starts; star-triage puts the clean `.git/config` back right after. The log lists
the setting names (`added: user.name user.email`, `value changed: remote.origin.url`), never
their values.

### The Actions tab says "There are no workflow runs yet"

**Cause:** nothing is wrong. The workflow only runs on its schedule or when you start it,
and the first scheduled run may be days away.

**Fix:** click **star-triage** in the left sidebar under "Workflows". Only then does the
**Run workflow** button appear on the right. Click it, set "Max repos" to `2`, and run.

Just clicked **Run workflow** (or setup started it) and GitHub says "Workflow run was
successfully requested" while the list still says there are no runs? Nothing is wrong: refresh
the page after a few seconds and the run appears.

### `No Claude credentials` (or `Add a CLAUDE_CODE_OAUTH_TOKEN ... secret`)

**Cause:** the secret is missing, misspelled, or added as a variable instead of a secret, or
added to a different repository than your data repo.

**Fix:** **Settings → Secrets and variables → Actions → Secrets tab**, in the repo that runs
the workflow. The name must match exactly.

### `GitHub Actions is not permitted to create or approve pull requests`

**Fix:** **Settings → Actions → General → Workflow permissions** → tick **Allow GitHub
Actions to create and approve pull requests** → Save.

### The push step fails with `403` or `Permission denied`

**Fix:** same page as above: select **Read and write permissions** → Save.

### A run added notes to an old pull request instead of opening a new one

This is on purpose. While a star-triage PR is open (usually because it queues a plugin for
install), later runs add their notes to it, and repos already in it are not reviewed again.
Merge it when you are ready; the next run starts a fresh PR.

### A notes-only PR was not merged automatically

The run page says why. Usual causes: an old `auto_merge: never` in `config.yml`, or a branch
protection rule on your main branch that requires reviews or checks. Merge it yourself, or
relax the rule for your own repo.

### The run fails at "Check setup (config.yml and secrets)"

The run page lists each problem with its fix. Most often: a typo or wrong indentation in
`config.yml`, or `Yes`/`No` written without quotes in the labels. Run
`python3 star-triage.py doctor` on your computer to see the same list.

### The run finished but found nothing new

Every starred repo already has a note in `tracker/`, or is listed under `ignore` in
`config.yml`. Star something new, or run with a larger "Max repos" if a backlog is waiting.

### The same repo got two notes

**Cause:** notes are matched by repository URL. If a repo was renamed or moved to another
owner (`facebook/x` became `meta/x`), the old URL in an existing note no longer matches your
star. Hand-written notes with a guessed URL cause the same thing.

**Fix:** keep the newer note, delete the older one, and make sure each note's `repo:` is the
exact URL GitHub shows today.

### Old notes have no "When you'd use it" section

Notes reviewed before 5.4.0 don't have it; new reviews always do (the run rejects a new note
without 2 or 3 examples). To add it to an old note, review it again: **Actions > star-triage >
Run workflow**, with `owner/name` in "Review these again". Setup no longer asks about you, and
the "about_me is empty" warning is gone; an `about_me` you already wrote still counts as extra
context.

### A note was rejected (`Note rejected` warning on the run page)

**Cause:** the note Claude wrote for that repo failed the checks (broken YAML, a missing
field, a label not in `config.yml`), or a file appeared in `tracker/` that this run was not
asked to write. The run leaves it out and publishes the other notes. The warning names the
file and the reason.

**Fix:** nothing to do. The repo has no note yet, so the next run reviews it again. If the
same repo is rejected every time, add it to `ignore` in `config.yml` or write its note by
hand. The run only fails when no note at all passed.

### `Integrity check failed`

**Cause:** the trusted scan copy changed while Claude was reviewing. The run stops before
anything is published. A reviewed repo may have talked the reviewer into it. (Before 5.2.1 this
also fired on every run because claude-code-action rewrites `.git/config`; update if you see it
on an old version.)

**Fix:** run the workflow again. If it repeats, review fewer repos per run (`max`) to find the
one that triggers it, and unstar or skip it.

### Every run opens a new review PR instead of adding to the open one

**Cause:** the run only adds to a PR that github-actions[bot] opened from a branch in your own
repo. A PR you opened yourself, a PR from a fork, or one opened with a personal token passed
as `github_token` is ignored.

**Fix:** merge or close the extra PRs. Leave `github_token` at its default so the bot opens them.

### Installed, but `'X' was not found where the tool was installed`

**Cause:** a setup step runs only the tool's own command, from the folder the tool was just
installed into. Nothing else on your PATH with that name is run.

**Fix:** if you trust it, run the command shown in the message yourself.

## The plugin and the launcher

### `not a valid GitHub owner/repo shorthand`

Two commands were pasted at once, so Claude Code read them as one marketplace name
(`thebeekeper/star-triage /plugin install ...`). Press Esc and run them one at a time, each on
its own:

```
/plugin marketplace add thebeekeper/star-triage
```

```
/plugin install star-triage@star-triage
```

### Which scope do I pick when installing the plugin?

`/plugin install star-triage@star-triage` opens a "Plugin Details" screen with a safety warning
and three choices. Pick **Install for you (user scope)**. The plugin installs your approved
tools whenever Claude Code starts, in any folder; project or local scope would only run it
inside one folder (project scope also adds it for everyone who works in that repo). The
warning is the general one for every plugin: this one runs a short Python check when Claude
Code starts and installs nothing you haven't approved. Picked another scope by mistake? Run
`/plugin`, uninstall star-triage, and install it again with user scope.

### `Couldn't find plugin "star-triage"` (Claude desktop app)

The app only knows plugins from marketplaces you've added. Run these in Claude Code, one at a
time:

```
/plugin marketplace add thebeekeper/star-triage
```

```
/plugin install star-triage@star-triage
```

If an older "star-triage" made by you shows up under Settings → Plugins, remove it; it isn't
this plugin.

### `The star-triage plugin was not found` (from `python3 star-triage.py ...`)

The launcher in your data repo finds the plugin's code through `~/.claude/star-triage.json`,
which the plugin writes each time Claude Code starts, or by searching `~/.claude/plugins/`.
Install the plugin (`/plugin marketplace add thebeekeper/star-triage`, then
`/plugin install star-triage@star-triage`), start Claude Code once, and try again.

### `No star-triage data repo found`

The scripts look for your data repo (the folder with `config.yml` and `tracker/`) in the
current folder first, then the one setup linked in `~/.claude/star-triage.json`. Run the
command from your data repo, or run setup once to link it.

### The startup hook says `python3`, `python` or `py` is not found

At startup the plugin tries `python3`, then `python`, then `py`, and uses the first that works.
If none does, install Python 3.9 or newer. On Windows, use python.org with **Add python.exe to
PATH** ticked; if Windows opens the Microsoft Store instead, turn off the `python.exe` alias in
*Settings → Apps → Advanced app settings → App execution aliases*. On Windows the hook runs in
Git Bash, which comes with Git for Windows.

### The plugin is on an old version

The plugin stays on its version until you update it. In a terminal:

```
claude plugin marketplace update star-triage
claude plugin update star-triage@star-triage
```

Then restart Claude Code. The doctor says when a newer version is out.

### `star-triage on this computer is X, your reviews use Y`

The Action on GitHub (`@v1`) updates itself; the plugin on your computer is one version behind
until it updates. Since 5.5.0 it updates itself when Claude Code starts (you see `updating
star-triage to X.Y.Z in the background; restart Claude Code to use it`), so you see this message
only when that is off (`auto_update: false` in config.yml), was already tried today, or failed
(`star-triage could not update itself`, details in `.star-triage/sync.log`). Update by hand, in
a terminal:

```
claude plugin marketplace update star-triage
claude plugin update star-triage@star-triage
```

Then restart Claude Code.

### The start message says `X installs after 11:52 (1 h safety delay)`

That's your star-triage mode (auto or auto-plus) at work, not Claude Code's own auto mode. The
item was reviewed less than `auto_wait_hours` ago (1 in configs made since 5.5.0; a config with
no such line uses 6); it installs the first time Claude Code starts after that time (your
computer's local time). Don't want to wait: `/star-triage:sync now` (or `python3 star-triage.py
sync --now`) installs it at once. That skips only the delay: the daily limit stays, and High-risk
and blocked items still need you. To stop it instead, untick `approve` on its note
(`python3 star-triage.py unapprove owner/name`), or run `python3 star-triage.py pause`.
"wait for tomorrow (daily limit N)" means `auto_daily_limit` was reached today. To shorten the
wait for good: `python3 star-triage.py schedule --install-wait 0` (or `/star-triage:schedule`),
which sets `auto_wait_hours` (0 = no wait) and pushes it. Items that share a time are named
together: "ponytail and brag install after 18:58".

### `Unable to resolve action thebeekeper/star-triage@v1`

Your workflow can't find the Action. Check the line in `.github/workflows/star-triage.yml` reads
exactly `uses: thebeekeper/star-triage@v1`, then run the workflow again. If you use a fork or
your own copy of the engine, point `uses:` at that repo, make sure it has a `v1` tag, and, if
it's private, allow access under *its Settings → Actions → General → Access*.

### My copy still has `scripts/`, `prompts/` and `docs/`

That's the v4 layout, where every copy carried the code. Run setup with `--migrate` (see
"Upgrading from v4" in the README); it removes the copies and keeps your notes and approvals.

### Plugin updates: `updating star-triage to X.Y.Z in the background`

When your reviews come from a newer star-triage than the plugin on this computer, the plugin
runs `claude plugin marketplace update star-triage` and `claude plugin update
star-triage@star-triage` in the background, at most once a day. Restart Claude Code to use the
new version. The next start says `updated star-triage to X.Y.Z` (or `could not update itself`,
with the commands to run by hand). Turn it off with `auto_update: false` in config.yml.

Setup also turns on Claude Code's own auto-update for the star-triage marketplace, by setting
the documented `autoUpdate` field on its `extraKnownMarketplaces` entry in
`~/.claude/settings.json` (a backup goes to `settings.json.bak`). It changes only that field,
and only when the entry already exists. To turn it off: `/plugin` > Marketplaces >
star-triage > Disable auto-update, or `auto_update: false` and run setup again.

## Installing approved items

Start with `python3 star-triage.py sync --status`: it lists everything that can be installed,
whether it is approved, and whether it is on this computer. `--dry-run` shows what a sync
would do. Every action and error is in `.star-triage/sync.log`.

### Nothing installs by itself

Automatic installs run when Claude Code **starts**, through the star-triage plugin. Check
`/plugin` lists star-triage as installed and enabled, and that `--status` says "Automatic sync
when Claude Code starts: on". Then quit Claude Code completely and start it again. The check runs at most every 5 minutes. Messages about
what was installed appear the next time you start Claude Code.

### Auto mode didn't install X

Run `python3 star-triage.py sync --status` (the mode, whether it is paused, and each item's
state) and `python3 star-triage.py history` (what was installed and why). The usual reasons:

- **The safety delay.** An item installs only once its review is `auto_wait_hours` old
  (1 in new configs, 6 when the line is missing). The start message says when, e.g. "X installs
  after 11:52 (1 h safety delay)". It installs at a later Claude Code start, or at once with
  `/star-triage:sync now`.
- **The daily limit.** At most `auto_daily_limit` items (default 5) install per day on one
  computer. The rest install on the following days.
- **Paused.** `python3 star-triage.py resume` turns automatic installs back on. The doctor
  warns while it is paused.
- **The risk is too high for the mode.** `auto` approves Low risk only, `auto-plus` Low and
  Medium. Anything above that leaves its review PR open: merge it, or tick `approve` on the
  note. High risk is never installed automatically, in any mode: run
  `python3 star-triage.py sync` yourself.
- **Blocked.** A high-severity scan finding or bundled binaries block an item. Read its
  Security scan section; if you still want it, tick `approve`, run `python3 star-triage.py sync`
  and type `install`.
- **Not approved at all.** Only the verdict Install is approved by a review. Try, Optional and
  the rest wait for your tick.
- **Claude Code wasn't restarted.** Installs run when Claude Code starts, at most every 5 minutes.
- **The mode never reached GitHub.** `python3 star-triage.py mode` shows the mode on this
  computer. If its push failed, the workflow still uses the old mode: run `git push` in your
  data repo.

### `mode`, `schedule`, `pause`, `undo`, `history`, `list`, `approve` or `token` is not a command

The `star-triage.py` launcher in your data repo is older than the plugin (`schedule` came in
5.6.0, `list`, `approve`, `unapprove` and `token` in 5.5.0, `mode` and the rest in 5.2). The copy at
`~/.claude/star-triage.py` is always current: `python3 "$HOME/.claude/star-triage.py" list`. Run
`python3 star-triage.py setup --skip-config` and answer `y` when it offers to update the
launcher. Until then, `/star-triage:mode` in Claude Code does the same.

### `Nothing approved yet`

Nothing is approved on this computer yet. Merge a review PR that approves installs (then
`git pull`, or let the Obsidian Git plugin pull), or tick `approve` on a note in Obsidian.

### `needs npx (install Node.js)` or `needs uv`

Tools from npm need Node.js; tools from PyPI need uv (or pipx). On Windows:
`winget install OpenJS.NodeJS.LTS` and `winget install astral-sh.uv`. Elsewhere see
https://nodejs.org and https://docs.astral.sh/uv/. Open a new terminal, then sync again.

### `X.ps1 cannot be loaded because running scripts is disabled on this system` (Windows)

The tool installed fine. Windows PowerShell blocks the small launcher script that npm creates
for every global tool. Either run it as `X.cmd` (for example `ccusage.cmd daily`), or allow
local scripts for your account once, the usual setting for Node.js on Windows:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Downloaded scripts still need a
signature.

### `MCP server 'X' needs SOME_API_KEY`

That server needs your key, and star-triage never copies keys around. Run the command it
printed, with your key in place of `<your key>`.

### `already exists and is not from star-triage; skipped`

You already have a skill or MCP server with that name, installed some other way. star-triage
won't overwrite it. Remove yours first if you want the reviewed version.

### Something with an Install verdict wasn't approved

The note's **Install plan (automated)** section says why: a high-severity scan finding,
bundled binaries, High risk, a package whose npm or PyPI page doesn't link back to the repo,
or a URL or setup command that doesn't appear in the repo. Items marked "install by hand"
show the command in their Install section.

### `is blocked ... run python3 star-triage.py sync yourself to confirm it`

You ticked `approve` on an item the security scan blocked. It is never installed in the
background. Read its Security scan section; if you still want it, run
`python3 star-triage.py sync` in a terminal and type `install` when asked.

### `plugin 'X' is not in your marketplace list yet`

Your copy's `.claude-plugin/marketplace.json` predates that plugin's review (versions before
v4.1 left blocked plugins out). The next workflow run that reviews anything rebuilds the list;
then run `python3 star-triage.py sync` again.

### An install step failed

Check `.star-triage/sync.log` for the exact command and its error, and run that command
yourself to see the full output. On Linux and macOS, `npm install -g` can need a Node install
that doesn't require sudo (for example via nvm).

### Installed, but a plugin or MCP server doesn't show up

Restart Claude Code: plugins and MCP servers load at startup. Skills appear straight away.

### I want to remove something

Untick `approve` on its note (it is removed at the next sync), or run
`python3 star-triage.py sync --uninstall NAME`, which also unticks the note.

### Claude Code asked a question during `/star-triage:sync`

When Claude runs the sync for you, it can't answer prompts on your behalf. Run
`python3 star-triage.py sync` in your own terminal and answer there. The sync never auto-accepts.

### `sync --now` installed only some of what waits

`--now` (or `/star-triage:sync now`) skips only the safety delay. It still stops at the daily
limit ("waits for tomorrow"), never installs High-risk items in the auto modes or blocked items
in any mode, and does nothing while paused. For a High-risk or blocked item, run
`python3 star-triage.py sync` in your own terminal (a blocked one asks you to type `install`).
`--now` is recorded in the history like an automatic install, so `undo` removes it.

### I don't use Obsidian: how do I see and approve reviews?

`/star-triage:list` in Claude Code, or `python3 star-triage.py list` (`--waiting` for what needs
you, `--repo owner/name` for one review with its security findings). `python3 star-triage.py
approve owner/name` does exactly what ticking `approve` in Obsidian does: it sets the note's
`approve` line on this computer. Commit and push the note if you want GitHub to keep it (a
later re-review keeps only an `approve` it can see). `unapprove` unticks it. Answer "No" to
the Obsidian question in setup (or set `obsidian: false`) and setup skips the Obsidian tips.

### `list` says "Couldn't fetch the latest reviews"

`list`, `approve` and `unapprove` run `git pull --ff-only --autostash` first, like sync, so a
review merged on GitHub shows without a manual pull. That pull failed (offline, not logged in to
GitHub, or your folder has commits GitHub doesn't), so the table shows the notes on your
computer and may miss the newest. Run `git pull` in your repo folder to see why. "No reviews
yet" after a pull that worked means the first run's pull request isn't merged yet.

### The Kind column says `app`, `library` or `proxy / CLI`

Repos with nothing star-triage can install show the note's `type`, lowercase like the
installable kinds (`plugin`, `skill`, `MCP server`, `CLI tool`). Since 5.6.0 they share one style.

### `list` says `waiting for you`

The review found something to install, but nothing approves it yet: the verdict or risk is
outside your star-triage mode, or it's High risk (never automatic in the auto modes). Read it
with `list --repo owner/name`, then `approve owner/name` if you want it.

## The schedule (how often reviews run, how long installs wait)

### How often does star-triage check, and how long do installs wait?

Two timers. **Reviews:** GitHub checks for new stars every 3 hours by default; it is the `cron`
line in `.github/workflows/star-triage.yml`, and `check_every_hours` in config.yml keeps the same
number. **Installs** (auto modes only): a reviewed tool installs by itself `auto_wait_hours`
after its review (1 by default), the next time Claude Code starts. See both:
`python3 star-triage.py schedule` (or `/star-triage:schedule`). Change them:
`schedule --check-every 1` (1, 3, 6, 12 or 24) and `schedule --install-wait 0` (0 = no wait).
A check with no new stars costs no Claude usage; hourly checks use about 720 minutes of Actions
time a month, within GitHub's 2,000 free minutes for private repos.

### `schedule` says "git push failed" after changing how often it checks

The change edits the workflow file, and GitHub refuses workflow changes from a login without the
`workflow` scope. Run `gh auth refresh -s workflow`, then `git push` in your repo folder. Until
the push works, GitHub keeps checking on the old schedule.

### Doctor: `config.yml says check_every_hours: N, but the workflow checks ...`

Someone edited one of the two by hand. GitHub goes by the workflow's `cron` line. Set both to one
value: `python3 star-triage.py schedule --check-every N`. A custom cron (for example weekdays
only) is fine: delete `check_every_hours` from config.yml and the warning stops.

### GitHub ran the check later than the hour I chose

GitHub delays scheduled runs when it is busy, sometimes by 10 to 30 minutes, and can skip one
now and then. star-triage starts at minute 17 to avoid the busiest time. The next check catches
anything missed. Need a run now: *Actions → star-triage → Run workflow*.

## Viewing the repo

### My repo shows "404 Page not found"

**Cause:** private repositories return 404 to anyone who isn't signed in with access,
including you in a browser where you're signed out.

**Fix:** sign in to GitHub in that browser, or check the repo name and owner in the URL.
