# star-triage

You star repos on GitHub. star-triage scans each one for security risks, has Claude review it,
and writes one short note per repo into a private GitHub repo of yours. Claude Code plugins,
skills, MCP servers and command-line tools you approve then install on your computer by themselves.

```mermaid
flowchart LR
  A[You star a repo] --> B[Security scan and Claude review]
  B --> C[A note in your repo]
  C --> D[Installed on your computer, if approved]
```

It runs in your own GitHub Actions, every 3 hours. How it works and why it is safe:
[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Install

Do the steps in order. Every code block is one command: copy it, paste it, press Enter, then
go to the next one.

### 1. Check what you need

| You need | Check it | Get it |
| --- | --- | --- |
| Claude Code, and a Claude subscription (Pro or Max) or an Anthropic API key | `claude --version` | [claude.com/claude-code](https://claude.com/claude-code) |
| Python 3.9 or newer | Windows: `python --version`<br>Mac and Linux: `python3 --version` | [python.org](https://www.python.org/downloads/) (Windows: tick **Add python.exe to PATH**) |
| git | `git --version` | [git-scm.com](https://git-scm.com/downloads) |
| A GitHub account, and the GitHub CLI logged in to it | `gh auth status` | [cli.github.com](https://cli.github.com), then `gh auth login` |
| Node.js and uv (only for tools that come from npm or PyPI) | `node --version`, `uv --version` | [nodejs.org](https://nodejs.org), [docs.astral.sh/uv](https://docs.astral.sh/uv/) |

**You should see** a version number for each, and `gh auth status` says "Logged in to
github.com account ...". Star a few repos on GitHub now if you have none: with no stars, the
first run has nothing to review.

<details><summary>If it fails</summary>

- `python` opens the Microsoft Store, or isn't found: [Troubleshooting](docs/TROUBLESHOOTING.md#python-or-python3-is-not-found).
- `claude` isn't recognized on Windows: [Troubleshooting](docs/TROUBLESHOOTING.md#installing-claude-code-needed-once-for-the-token).

</details>

### 2. Add the marketplace

Start Claude Code (type `claude` in a terminal, or open the desktop app) and run:

```
/plugin marketplace add thebeekeper/star-triage
```

In the desktop app you can also use *Directory → Plugins → + → Add marketplace*.

**You should see** Claude Code confirm that the star-triage marketplace was added.

<details><summary>If it fails</summary>

- "not a valid GitHub owner/repo shorthand": two commands were pasted at once. Press Esc and
  paste only the line above. See [Troubleshooting](docs/TROUBLESHOOTING.md#not-a-valid-github-ownerrepo-shorthand).

</details>

### 3. Install the plugin

```
/plugin install star-triage@star-triage
```

A "Plugin Details" screen opens. It shows "By: Bee", "Components will be discovered at
installation" and a general safety warning that plugins can run code on your computer.
Choose **Install for you (user scope)**: star-triage installs your approved tools whenever
Claude Code starts, in any folder, so it must be installed for you, not for one project.

What the warning means here: each time Claude Code starts, the plugin runs a short Python
check. It installs nothing you haven't approved, and never asks for your tokens.

Then quit Claude Code and start it again, so the plugin loads.

**You should see** star-triage listed as installed when you type `/plugin`, and
`/star-triage:setup` among the suggestions when you type `/star-triage`.

<details><summary>If it fails</summary>

- `Couldn't find plugin "star-triage"`: do step 2 first. See [Troubleshooting](docs/TROUBLESHOOTING.md#the-plugin-and-the-launcher).
- Chose project or local scope by mistake: [Troubleshooting](docs/TROUBLESHOOTING.md#which-scope-do-i-pick-when-installing-the-plugin).

</details>

### 4. Run setup

Setup uses the GitHub account the GitHub CLI is logged in to: your repo, its settings and the
secret all go there. Check it is the right one (`gh auth status`) before you start. Then, in
Claude Code:

```
/star-triage:setup
```

Claude says which GitHub account it found, then asks four questions:

- **star-triage mode:** who approves installs. `auto-plus` is recommended (see
  [What happens next](#what-happens-next)). You can change it any time.
- **Repos per run:** 8, 5 or 3. Every repo you have starred is reviewed, this many every 3 hours.
- **Data repo:** create a new private repo (`my-stars`), or use one you already have.
- **Obsidian:** whether you read your notes in [Obsidian](https://obsidian.md). It's optional.

It then shows one plan and asks once. Answer yes and it does this:

| What | Where you can check or undo it |
| --- | --- |
| Creates the private repo `<you>/my-stars` in `Documents/my-stars` (or clones the repo you picked) | GitHub, and that folder |
| Turns on GitHub Actions for it, and **Allow GitHub Actions to create and approve pull requests** (each run puts its notes in a pull request) | the repo's *Settings → Actions → General → Workflow permissions* |
| Links your computer to the repo, and adds the `star-triage.py` launcher | `~/.claude/star-triage.json` and `~/.claude/star-triage.py` (`~` is your user folder) |
| Turns on Claude Code's auto-update for the star-triage marketplace | `autoUpdate` in `~/.claude/settings.json` (backup: `settings.json.bak`) |

Then it asks you for the token (step 5).

**You should see** a summary of what was done and "Not stored yet" for the Claude token.

<details><summary>If it fails</summary>

- "The GitHub CLI is not installed" or "not logged in": run `gh auth login` in a terminal,
  then `/star-triage:setup` again.
- Wrong GitHub account: `gh auth login` (or `gh auth switch`) to the right one, then run setup again.
- Anything else: [Troubleshooting, setup](docs/TROUBLESHOOTING.md#setup-script). Setup is safe to run again.

</details>

### 5. Store your Claude token

This is the one step outside Claude Code: `claude setup-token` prints your token, and it must
never pass through Claude. Open a **new terminal window** (PowerShell on Windows, Terminal on
Mac), not the Claude Code chat, and run:

<table>
<tr><th>Windows (PowerShell)</th><th>Mac and Linux</th></tr>
<tr><td>

```
python "$HOME/.claude/star-triage.py" token
```

</td><td>

```
python3 "$HOME/.claude/star-triage.py" token
```

</td></tr>
</table>

It opens your browser to log in to Claude, then prints a long token. Copy it and paste it at
the hidden prompt (nothing shows while you paste); it goes straight to GitHub as the secret
`CLAUDE_CODE_OAUTH_TOKEN`. Say yes when it offers to clear the screen. Using an Anthropic API
key instead of a subscription? Add ` --api-key` to the command.

**Never share that window:** no screenshots, no pasting it into a chat. Anyone with the token
can use your Claude subscription. If it was shared, run the same command again: it asks
"Replace it?", and you store a new token. Then revoke the old one (see
[Claude token was shared](docs/TROUBLESHOOTING.md#claude-token-was-shared)).

**You should see** "Secret saved." and then "Done. Go back to Claude Code and say so". The secret
is listed in your repo under *Settings → Secrets and variables → Actions*.

<details><summary>If it fails</summary>

- "only runs in your own terminal": you ran it inside Claude Code. Use a separate terminal window.
- `claude` isn't recognized: [Troubleshooting](docs/TROUBLESHOOTING.md#installing-claude-code-needed-once-for-the-token).

</details>

### 6. The first run

Go back to Claude Code and say you're done. Claude checks the secret is there (by name only)
and starts the first run, with up to 3 of your newest stars.

The run may take a minute to appear: GitHub can say "Workflow run was successfully requested"
while the list still says there are no runs. Refresh the page after a few seconds.

**You should see** a green run under your repo's **Actions** tab, then a pull request with one
note per reviewed repo. It merges itself, or waits for you, depending on your mode (next
section).

<details><summary>If it fails</summary>

- "workflow not found" or "hasn't registered": wait a minute, then on GitHub click
  *Actions → star-triage → Run workflow*. See [Troubleshooting, GitHub Actions](docs/TROUBLESHOOTING.md#github-actions).
- A red run: its page says why. Check your setup with `/star-triage:setup` (it is safe to run again).

</details>

## What happens next

1. **First run:** up to 3 repos are reviewed (step 6).
2. **Every 3 hours:** the next repos you starred are reviewed, newest first, "repos per run" at
   a time, until all of them are done. After that, only new stars. A check with no new stars
   uses no Claude usage.
3. **A pull request per run** holds the new notes. It merges itself unless it approves an
   install your mode doesn't cover; then it waits for you to merge it. Until you do, later
   runs add to the same pull request.
4. **Notes reach your computer** when Claude Code starts: the plugin pulls your repo.
5. **Installs happen when Claude Code starts**, for everything approved, after the safety delay
   in the auto modes. The start message says what was installed and what waits.

Your **star-triage mode** decides what installs without you. Change it with `/star-triage:mode`.

| Mode | Installs by itself | Waits for you |
| --- | --- | --- |
| `auto-plus` (recommended) | **Low and Medium** risk, any verdict except Skip and Not now | Skip and Not now verdicts: tick `approve` if you want one |
| `auto` | **Low** risk, any verdict except Skip and Not now | Medium risk: the verdict Install installs once you merge the pull request; others when you tick `approve` |
| `manual` | Nothing on its own. The verdict **Install** installs once you merge the pull request | Everything else: tick `approve` |

- **High risk and blocked items never install by themselves**, in any mode. Blocked means the
  scan found something serious (or bundled binaries); you can still install it from a terminal
  by typing `install` to confirm.
- **In the auto modes** an item waits a safety delay after its review (1 hour; `auto_wait_hours`),
  and at most 5 install per day (`auto_daily_limit`). The delay is your chance to stop one.

## Daily use

### Read your notes

Each note says what the repo is, whether it lives up to its claims, how risky it is (with
file and line), when you'd use it, and a verdict: Install, Try, When needed, Optional,
Portfolio only, Not now or Skip.

- **With Obsidian** (1.9 or newer): open your repo folder (`Documents/my-stars`) as a vault, then
  `tracker/Star Triage.base` for the Inbox, Installs and other views.
- **Without Obsidian:** `/star-triage:list` in Claude Code shows a table of your reviews and
  what each one is waiting for. Ask "tell me about owner/name" for one in full.
- **On GitHub:** the `tracker/` folder of your repo.

### Approve or stop one install

- **Obsidian:** tick `approve` on the note to install it; untick it to stop or remove it.
- **Claude Code:** say "approve owner/name" or "don't install owner/name" (the `/star-triage:list`
  skill does it).
- **Terminal**, in your repo folder: the commands below. They change the note on this
  computer, like the checkbox. Commit and push the note to keep the change on GitHub.

| | Windows | Mac and Linux |
| --- | --- | --- |
| Approve | `python star-triage.py approve owner/name` | `python3 star-triage.py approve owner/name` |
| Stop or remove | `python star-triage.py unapprove owner/name` | `python3 star-triage.py unapprove owner/name` |
| What waits for you | `python star-triage.py list --waiting` | `python3 star-triage.py list --waiting` |

A pull request that waits for you also approves: read its notes (the *Install plan* and
*Security scan* sections say what would be installed and why), then merge it.

### Installs

Installs happen by themselves when Claude Code starts, after the safety delay in the auto
modes. Skills work at once; plugins and MCP servers load the next time Claude Code starts.
To do it yourself, in your repo folder:

| | Windows | Mac and Linux |
| --- | --- | --- |
| Install what's approved now | `python star-triage.py sync` | `python3 star-triage.py sync` |
| Skip the safety delay | `python star-triage.py sync --now` | `python3 star-triage.py sync --now` |
| Preview, change nothing | `python star-triage.py sync --dry-run` | `python3 star-triage.py sync --dry-run` |
| What's installed and what waits | `python star-triage.py sync --status` | `python3 star-triage.py sync --status` |

In Claude Code: `/star-triage:sync` (or `/star-triage:sync now`). `--now` skips only the delay:
the daily limit, pause, and the High-risk and blocked rules stay.

### Change the mode, pause, undo

| | Windows | Mac and Linux |
| --- | --- | --- |
| Show or change the mode | `python star-triage.py mode` | `python3 star-triage.py mode` |
| Stop everything automatic on this computer | `python star-triage.py pause` | `python3 star-triage.py pause` |
| Start it again | `python star-triage.py resume` | `python3 star-triage.py resume` |
| Remove the last automatic batch | `python star-triage.py undo` | `python3 star-triage.py undo` |
| The last 20 automatic installs and removals | `python star-triage.py history` | `python3 star-triage.py history` |

In Claude Code: `/star-triage:mode`, or say "pause star-triage", "undo what star-triage
installed". `mode auto-plus` (or `auto`, `manual`) switches without the menu; it changes
`mode` in `config.yml`, commits and pushes. `undo` asks before it removes anything.

## Updating

- **The GitHub Action** updates itself: your workflow uses `thebeekeper/star-triage@v1`.
- **The plugin** updates itself too. When your reviews come from a newer star-triage, it updates
  in the background when Claude Code starts (at most once a day) and asks you to restart Claude
  Code. Setup also turns on Claude Code's own auto-update for it. `auto_update: false` in
  `config.yml` turns both off.

To update the plugin by hand, run these in a terminal, one at a time, then restart Claude Code:

```
claude plugin marketplace update star-triage
```

```
claude plugin update star-triage@star-triage
```

<details><summary>Stop or uninstall everything</summary>

Run the commands in your repo folder (`python3` instead of `python` on Mac and Linux), in this order.

1. **Stop the reviews:** on GitHub, *Actions → star-triage → ⋯ → Disable workflow* (or
   `gh workflow disable star-triage.yml`). `gh workflow enable star-triage.yml` turns it back on.
2. **Remove what it installed:** `python star-triage.py sync --status` lists it; remove each with
   `python star-triage.py sync --uninstall NAME`. Do this before step 3: the launcher needs the plugin.
3. **Remove the plugin**, in a terminal: `claude plugin uninstall star-triage@star-triage`, then
   `claude plugin marketplace remove star-triage`. If reviewed plugins were installed, also
   `claude plugin marketplace remove my-stars` (your `marketplace_name`). Then delete
   `~/.claude/star-triage.json` and `~/.claude/star-triage.py`.
4. **Remove your Claude token from GitHub:** *Settings → Secrets and variables → Actions*, or
   `gh secret delete CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`).
5. **Delete your notes, if you want:** delete the repo on GitHub (*Settings → Danger Zone*) and
   its folder. Your stars are not touched.

</details>

## Reference

### Commands

Run them in your repo folder: `python star-triage.py <command>` on Windows,
`python3 star-triage.py <command>` on Mac and Linux. `python star-triage.py` alone lists them.

| Command | What it does | In Claude Code |
| --- | --- | --- |
| `sync` | Install, update or remove what you approved | `/star-triage:sync` |
| `sync --now` | The same, without the safety delay | `/star-triage:sync now` |
| `sync --dry-run` | Show what `sync` would do; change nothing | |
| `sync --status` | Everything installable, and its state on this computer | |
| `sync --uninstall NAME` | Remove one item and untick its note | |
| `list` | Your reviews, newest first (`--waiting`, `--repo owner/name`) | `/star-triage:list` |
| `approve owner/name` | Approve an install, like ticking `approve` | "approve owner/name" |
| `unapprove owner/name` | Untick it; an installed item is removed | "don't install owner/name" |
| `mode` | Show or change the mode (`mode auto-plus`, `auto`, `manual`) | `/star-triage:mode` |
| `pause`, `resume` | Stop or restart everything automatic on this computer | `/star-triage:mode` |
| `undo` | Remove the last automatic batch and untick those notes | `/star-triage:mode` |
| `history` | The last 20 automatic installs and removals, with the reason | `/star-triage:mode` |
| `doctor` | Check your setup and say how to fix what is wrong | `/star-triage:setup` |
| `setup` | Guided setup in the terminal; safe to run again | `/star-triage:setup` |
| `token` | Store your Claude token as a GitHub secret (your own terminal only) | |

### Settings (`config.yml` in your repo)

Edit, commit and push. The next run uses the new values.

| Key | Default | What it does |
| --- | --- | --- |
| `mode` | your answer in setup | `manual`, `auto` or `auto-plus` (see [What happens next](#what-happens-next)); missing means `manual` |
| `max_per_run` | `8` | Repos reviewed per run |
| `model` | `sonnet` | Claude model for reviews: `sonnet`, `opus` or `haiku` |
| `auto_wait_hours` | `1` | Auto modes: hours after a review before it installs (`0` = no delay) |
| `auto_daily_limit` | `5` | Auto modes: most automatic installs per day on one computer |
| `obsidian` | your answer in setup | `false` skips the Obsidian tips |
| `auto_update` | `true` | `false` stops the plugin updating itself |
| `ignore` | `[]` | Repos never to review (`owner/name`) |
| `github_user` | your repo's owner | Whose stars to read |
| `contexts` | `General` | Optional: your projects, each becomes a `relevant_to` label |
| `marketplace_name` | `my-stars` | Name of the list of reviewed plugins |
| `install_scope` | `user` | Scope for reviewed plugins: `user`, `project` or `local` |
| `labels` | | The label values notes may use |

The schedule is the `cron` line in `.github/workflows/star-triage.yml` (every 3 hours). To review
repos again, run the workflow on GitHub with **retriage** set to `owner/name` (comma separated).

## More

- [Troubleshooting](docs/TROUBLESHOOTING.md): every problem people have hit, with the fix.
- [How it works](docs/HOW-IT-WORKS.md): the security model, cost, files, setting up by hand, FAQ.
- [Features](docs/FEATURES.md) and [Changelog](CHANGELOG.md).

## License

MIT. Made by Bee ([@thebeekeper](https://github.com/thebeekeper)).
