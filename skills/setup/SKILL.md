---
name: setup
description: Set up star-triage from the chat, or check and fix an existing setup. Asks four questions, shows one plan, then runs setup itself (creates or links the data repo, applies the GitHub settings, starts the first run). The only terminal step is the Claude token, through one short command. Run manually with /star-triage:setup.
disable-model-invocation: true
---

# /star-triage:setup

Get a new user from "I just installed the plugin" to "the first run worked", inside this chat.
`setup.py` does the work; you ask the questions, show the plan once, and run it with flags.
The engine is at `${CLAUDE_PLUGIN_ROOT}`. Below, `PY` is `python3` (on Windows `python`, or `py`
if that is not found; use the one that works in every command).

## Hard rules

- Never ask for, read, print or store a token or API key. Never run `claude setup-token`,
  `gh secret set`, `gh auth token`, `gh auth status`, `star-triage.py token`, or anything else
  that shows or sets a secret. The token step is the user's, in their own terminal.
- Run `setup.py` with `--yes` only after the user said yes to the plan you showed. Never add
  `--yes` to any other command.
- Don't pick a star-triage mode for the user, and don't ask about them (their projects, their
  job). Reviews are judged on general quality and safety.
- Don't edit config.yml, notes, `installs.json` or the workflow yourself: setup.py does it.

## Steps

1. Run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/paths.py" launcher`. It saves the stable launcher at
   `~/.claude/star-triage.py` (used for the token command below) and prints the terminal setup
   command, a fallback if this chat flow can't be used.

2. Check the GitHub CLI: `gh --version`, then `gh api user --jq .login` (prints only the login).
   - Not installed: tell the user the one command, then stop: Windows
     `winget install --id GitHub.cli`, Mac `brew install gh`, others https://cli.github.com.
     After installing: a new terminal, `gh auth login`, then `/star-triage:setup` again.
   - Not logged in (the second command fails): tell them to run `gh auth login` in a terminal
     (or type `! gh auth login` here), then `/star-triage:setup` again. Stop.
   - Logged in: say which account. Settings and secrets go to that account's repos.

3. Run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"` and note whether a data repo is already
   set up (a "Data repo:" and "GitHub repo: owner/name" line). Old v4 copy ("old all-in-one
   layout")? Tell them to run `PY star-triage.py setup --migrate` in a terminal in that repo,
   and stop. "GitHub Actions hasn't registered" the workflow? Tell them to run
   `PY star-triage.py setup --repair` in a terminal in the data repo.

4. Ask these four questions in ONE AskUserQuestion call:
   - **star-triage mode** (say "star-triage mode", not "auto mode"): first option
     `auto-plus (Recommended)`: "Low and Medium-risk tools install by themselves after a short
     safety delay (1 h), at most 5 a day; High-risk and blocked items always wait for you."
     Then `auto`: "Only Low-risk tools install by themselves." Then `manual`: "You approve
     every install." For an existing repo whose mode the doctor showed, add "(current)".
   - **Repos per run**: `8 (default)`, `5`, `3` ("keeps Claude usage low"). Every repo starred
     so far gets reviewed, this many per run, every 3 hours.
   - **Data repo**: if the doctor found one, `Use owner/name (Recommended)` and `Create a new
     one`; otherwise `Create a new private repo "my-stars" (Recommended)` and `Use one I already
     have` (they type owner/name, or a different new name, in "Other").
   - **Obsidian**: "Do you read your notes in Obsidian?" `Yes` / `No, show them here`
     (No: /star-triage:list shows the reviews instead).

5. Build the flags: `--mode <mode> --max <n> --obsidian|--no-obsidian`, plus `--repo owner/name`
   (existing) or `--new-repo <name>` (new; add `--folder <parent>` only if they named a folder;
   the default is ~/Documents). Run, with a 10-minute Bash timeout:
   `PY "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" <flags>`
   Without `--yes` it changes nothing and prints "Here is what setup will do:". Show that plan
   in the chat once, as a short list, and ask once: go ahead? If it printed a problem instead
   (bad flag, gh not ready, folder exists), explain it and ask again.

6. After a clear yes, run the same command with `--yes` added. Summarise what it did, in plain
   words. If it stopped with an error, show its last lines and the fix it printed.

7. **The token** (when setup said "Not stored yet"): this is the one terminal step, because
   `claude setup-token` prints the token and it must never pass through Claude. Tell the user
   to open a terminal (PowerShell on Windows, Terminal on Mac), NOT this chat or `!`, and run
   exactly one line:
   - Windows: `python "$HOME/.claude/star-triage.py" token`
   - Mac and Linux: `python3 "$HOME/.claude/star-triage.py" token`

   Warn them: it opens the browser to log in, then shows a long token on screen; copy it and
   paste it at the hidden prompt. Don't paste it into this chat, don't screenshot or share
   that window; the command offers to clear the screen afterwards. (An Anthropic API key
   instead of a subscription: add ` --api-key` to the line.) Then wait for them to say done.

8. Check the secret by name only:
   `gh secret list --repo owner/name --json name --jq ".[].name"`. You need
   `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`. Missing: say so and repeat step 7.
   Present: run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --repo owner/name --yes` (10-minute
   timeout). This is safe to repeat: with the token stored it starts the first run and prints
   its link.

9. Run the doctor once more and say what is left, if anything. Then the next steps: the first
   run takes 2 to 5 minutes; merge its pull request if one waits (depends on the mode); notes
   reach the computer with `git pull` (sync does that by itself). Obsidian users: open the data
   repo folder as a vault, then `tracker/Star Triage.base`. Others: `/star-triage:list`.
   Problems and fixes: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md

## Changing settings later

For an existing data repo the same flags work: e.g. `setup.py --max 3` prints the plan, then
`--yes` commits and pushes config.yml. The mode alone: use /star-triage:mode. Projects
(`contexts`, optional `relevant_to` labels) are edited in config.yml by the user; quote strings
that contain a colon and keep `General`.
