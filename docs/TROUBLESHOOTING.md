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
`max_per_run` per run (default 8), every 3 hours, until none are left. Lower `max_per_run` in
`config.yml` to go slower, list repos under `ignore` to leave them out, or disable the workflow
to pause (`gh workflow disable star-triage.yml`).

### Setup says it "needs a terminal you can type in"

Setup was started without a keyboard attached (from Claude Code, a script, or with input piped
in), so it stops instead of answering its own questions. Open a terminal window and run the same
command there. `--check` still works anywhere.

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

### The Actions tab says "There are no workflow runs yet"

**Cause:** nothing is wrong. The workflow only runs on its schedule or when you start it,
and the first scheduled run may be days away.

**Fix:** click **star-triage** in the left sidebar under "Workflows". Only then does the
**Run workflow** button appear on the right. Click it, set "Max repos" to `2`, and run.

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

### A note was rejected (`Note rejected` warning on the run page)

**Cause:** the note Claude wrote for that repo failed the checks (broken YAML, a missing
field, a label not in `config.yml`), or a file appeared in `tracker/` that this run was not
asked to write. The run leaves it out and publishes the other notes. The warning names the
file and the reason.

**Fix:** nothing to do. The repo has no note yet, so the next run reviews it again. If the
same repo is rejected every time, add it to `ignore` in `config.yml` or write its note by
hand. The run only fails when no note at all passed.

### `Integrity check failed`

**Cause:** the trusted scan copy or `.git/config` changed while Claude was reviewing. The run
stops before anything is published. A reviewed repo may have talked the reviewer into it.

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

### `Couldn't find plugin "star-triage"` (Claude desktop app)

The app only knows plugins from marketplaces you've added. Run the two commands in Claude Code:

```
/plugin marketplace add thebeekeper/star-triage
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

### `Unable to resolve action thebeekeper/star-triage@v1`

Your workflow can't find the Action. Check the line in `.github/workflows/star-triage.yml` reads
exactly `uses: thebeekeper/star-triage@v1`, then run the workflow again. If you use a fork or
your own copy of the engine, point `uses:` at that repo, make sure it has a `v1` tag, and, if
it's private, allow access under *its Settings → Actions → General → Access*.

### My copy still has `scripts/`, `prompts/` and `docs/`

That's the v4 layout, where every copy carried the code. Run setup with `--migrate` (see
"Upgrading from v4" in the README); it removes the copies and keeps your notes and approvals.

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
  (default 6). The start message says "1 waiting (6 h safety delay)". It installs at a later
  Claude Code start.
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

### `mode`, `pause`, `undo` or `history` is not a command

The `star-triage.py` launcher in your data repo is from before 5.2. Run
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

## Viewing the repo

### My repo shows "404 Page not found"

**Cause:** private repositories return 404 to anyone who isn't signed in with access,
including you in a browser where you're signed out.

**Fix:** sign in to GitHub in that browser, or check the repo name and owner in the URL.
