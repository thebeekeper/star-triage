---
name: setup
description: Set up star-triage from the chat, or check and fix an existing setup. Asks a few questions, shows one plan, then runs setup itself (creates or links the data repo, applies the GitHub settings, starts the first run). The only terminal step is the Claude token, through one short command. Run manually with /star-triage:setup.
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
   - Logged in: say which account. The data repo, its settings and the secret all go to that
     account. If it's the wrong one, tell them to run `gh auth switch` (or `gh auth login`) in a
     terminal, then `/star-triage:setup` again, and stop.

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
     so far gets reviewed, this many per run. (The first run always takes 3, so they see
     results in a few minutes.)
   - **Data repo**: if the doctor found one, `Use owner/name (Recommended)` and `Create a new
     one`; otherwise `Create a new private repo "my-stars" (Recommended)` and `Use one I already
     have` (they type owner/name, or a different new name, in "Other").
   - **Obsidian**: "Do you read your notes in Obsidian?" `Yes` / `No, show them here`
     (No: /star-triage:list shows the reviews instead).

   Then ask the timing questions in a second AskUserQuestion call:
   - **How often should GitHub check for new stars?** `Every 3 hours (Recommended)`,
     `Every hour`, `Every 6 hours`, `Once a day` (12 hours: they type it in "Other"). Say: a check
     with no new stars stops before Claude runs, so it uses no Claude usage, only about a minute
     of GitHub Actions time. Flag: `--check-every 3` (1, 3, 6, 12 or 24).
   - Only if they chose `auto` or `auto-plus`: **How long should a reviewed tool wait before it
     installs by itself?** `1 hour (Recommended)`, `No wait`, `6 hours`, `24 hours`. Say: the wait
     is time to untick it or pause first. Flag: `--install-wait 1` (0 = no wait, 1, 6 or 24).

5. Build the flags: `--mode <mode> --max <n> --obsidian|--no-obsidian --check-every <h>`, plus
   `--install-wait <h>` in the auto modes, plus `--repo owner/name`
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
   to open a new terminal window (PowerShell on Windows, Terminal on Mac), NOT this chat or `!`,
   and run exactly one line:
   - Windows: `python "$HOME/.claude/star-triage.py" token`
   - Mac and Linux: `python3 "$HOME/.claude/star-triage.py" token`

   Tell them what they will see: the browser opens to log in to Claude, then a long token is
   printed; copy it and paste it at the hidden prompt (nothing shows while pasting). It ends
   with "Secret saved." and "Done. Go back to Claude Code and say so". Say yes when it offers
   to clear the screen. Never share that window: no screenshots, nothing pasted into this
   chat. If it was shared, run the same line again and answer `y` to "Replace it?". (An
   Anthropic API key instead of a subscription: add ` --api-key` to the line.) Then wait for
   them to say done.

8. Check the secret by name only:
   `gh secret list --repo owner/name --json name --jq ".[].name"`. You need
   `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`. Missing: say so and repeat step 7.
   Present: run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --repo owner/name --yes` (10-minute
   timeout). This is safe to repeat: with the token stored it starts the first run and prints
   its link.

9. Run the doctor once more and say what is left, if anything. Then the next steps, short:
   - The run may take a minute to appear on the Actions page: GitHub can say "Workflow run was
     successfully requested" while the list still shows no runs; refresh after a few seconds.
     No stars yet? Star a few repos first: with none, the run has nothing to review.
   - They should see a green run, then a pull request with one note per repo. State the rule
     for the mode they chose, don't hedge ("may be up to you"):
     - manual: "It merges itself when it only adds notes. If it approves installs, it waits for
       you: merging it approves them."
     - auto: "It merges itself unless it approves an install above Low risk; then it waits for you."
     - auto-plus: "It merges itself unless it approves an install above Medium risk; then it
       waits for you."
   - Say why the first run is small: it reviews 3 so they see results in a few minutes; after
     that, "repos per run" each check.
   - Both timers, with their numbers (setup prints them under "Timers"): reviews run every N
     hours (the cron line in the workflow); in the auto modes a reviewed tool installs by itself
     N h after its review, the next time Claude Code starts (`auto_wait_hours`).
     `/star-triage:sync now` installs what is approved at once. Change either later with
     `/star-triage:schedule`.
   - Reading notes: Obsidian users open the data repo folder as a vault, then
     `tracker/Star Triage.base`. Others: `/star-triage:list`.
   - Problems and fixes: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md

## Changing settings later

For an existing data repo the same flags work: e.g. `setup.py --max 3` prints the plan, then
`--yes` commits and pushes config.yml. The mode alone: use /star-triage:mode. The check
interval or the install wait alone: use /star-triage:schedule. Projects
(`contexts`, optional `relevant_to` labels) are edited in config.yml by the user; quote strings
that contain a colon and keep `General`.
