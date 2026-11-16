---
name: setup
description: Set up star-triage, or check and fix an existing setup. Checks the computer, then hands the user one short setup command to run in their own terminal (setup creates the data repo, applies the GitHub settings and stores the token). Can also edit config.yml (projects, star-triage mode) for an existing data repo. Run manually with /star-triage:setup.
disable-model-invocation: true
---

# /star-triage:setup

Get a new user from "I just installed the plugin" to "the first run worked", with as few
manual steps as possible. `setup.py`, run by the user in their own terminal, does everything
that creates repos, changes GitHub settings or touches a token. You check, explain, and (for an
existing data repo) help with config.yml.

The engine is at `${CLAUDE_PLUGIN_ROOT}`.

## Hard rules

- Never ask for, read, print or store a token or API key. Never run `claude setup-token`,
  `gh secret set`, or anything that sets a secret. Those happen in setup.py, in the user's own
  terminal, where the token goes straight from Claude's login page to GitHub.
- Never create repos, change GitHub settings or push yourself. setup.py does that, after
  showing its plan.
- Only edit `config.yml` in the user's data repo. Don't touch the plugin's own files, notes,
  `installs.json` or the workflow.
- Don't ask the user about themselves. Reviews are judged on general quality and safety, and
  each note lists 2 or 3 examples of when you'd use the tool.

## Steps

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/paths.py" launcher` (on Windows `python`, or `py`
   if that is not found; use the one that works in every command below). It saves a small
   launcher at `~/.claude/star-triage.py` and prints the setup command, e.g.
   `python "$HOME/.claude/star-triage.py" setup`. That command is short and stays the same
   after plugin updates. Always give the user this printed command, never a path with the
   plugin's version number in it.

2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"` and summarise the result in plain
   words: what is done, what is missing. "Not set up yet" is the normal state for a new user,
   not an error.

3. **Not set up yet:** tell the user to run the printed command in their own terminal
   (PowerShell on Windows, Terminal on Mac): type `/exit` here, or open another terminal
   window, and paste it. It must run in the terminal logged in to the GitHub account they
   want to use (`gh auth status` shows which). Explain what happens: a few questions (repo
   name, optional projects, the star-triage mode, Claude subscription or API key), then ONE
   plan and "Go ahead? [Y/n]". Then setup creates a private GitHub repo on branch `main`,
   turns on the GitHub settings it needs (printing what each is, why, and where it lives on
   GitHub), runs `claude setup-token` for the token (tell them: don't share or screenshot that
   part; setup offers to clear the screen), waits until GitHub has registered the workflow,
   and starts the first run. Every repo they have starred so far gets reviewed,
   `max_per_run` per run (default 8) every 3 hours. After the first run: check it is green,
   merge the pull request if one is waiting, and run `git pull`. Want every step asked
   separately? Add `--step-by-step`. Stop here and wait.

4. **Old v4 copy** (the doctor says "old all-in-one layout"): tell the user to run, from that
   repo's folder, `python3 star-triage.py setup --migrate` (or the printed command plus
   `--migrate`). It removes the copied code, adds the small workflow and launcher, and keeps
   notes, config and approvals.

5. **"GitHub Actions hasn't registered" the workflow:** tell them to run
   `python3 star-triage.py setup --repair` in their data repo. It pushes a one-character change
   to the workflow file, which makes GitHub look again.

6. **Data repo exists and they want to change settings:** ask, one short question at a time:
   - Their projects, if they want `relevant_to` labels (optional). Short names (2 to 4 words),
     one line each. Always keep `General`.
   - How many repos per run (default 8; 3 to 5 keeps usage low).
   - The star-triage mode (not Claude Code's own auto mode): `manual` (default: they approve
     every install), `auto` (Low risk installs by itself) or `auto-plus` (Low and Medium).
     High-risk and blocked items never install automatically. Never pick an auto mode for them.
   Edit config.yml with the answers: `contexts`, `max_per_run`, `mode`. Quote strings that
   contain a colon. Keep the comments and the `labels` section. Leave `github_user` empty unless
   they want someone else's stars. An existing `about_me` can stay; don't add one. Run the
   doctor again and fix any config problem it reports. Then tell them to run in their data repo:
   `python3 star-triage.py setup --skip-config`, which commits and pushes the config and
   finishes any GitHub step still missing.

7. When they come back, run the doctor once more and say what is left, if anything. Problems
   and fixes: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md
