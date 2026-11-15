---
name: setup
description: Set up star-triage, or check and fix an existing setup. Creates or links the user's data repo, interviews them for config.yml, and hands the GitHub and token steps to setup.py in their own terminal. Run manually with /star-triage:setup.
disable-model-invocation: true
---

# /star-triage:setup

Get a new user from "I just installed the plugin" to "the first run worked", with as few
manual steps as possible. You do the conversation and config.yml. `setup.py`, run by the user
in their own terminal, does everything that creates repos, changes GitHub settings or touches
a token.

The engine is at `${CLAUDE_PLUGIN_ROOT}`. Setup is `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py` and
the check is `${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py`.

## Hard rules

- Never ask for, read, print or store a token or API key. Never run `claude setup-token`,
  `gh secret set`, or anything that sets a secret. Those happen in setup.py, in the user's own
  terminal, where the token goes straight from Claude's login page to GitHub.
- Never create repos, change GitHub settings or push yourself. setup.py does that, asking first.
- Only edit `config.yml` in the user's data repo. Don't touch the plugin's own files, notes,
  `installs.json` or the workflow.

## Steps

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"` (on Windows `python`, or `py` if that
   is not found; use the one that works in every command below) and
   summarise the result in plain words: what is done, what is missing.

2. **No data repo yet** (the doctor says none was found): tell the user to run this in their own
   terminal (PowerShell on Windows), with the real path filled in (`python` on Windows):

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py"

   Explain what it does: creates a private GitHub repo for their notes (needs the GitHub CLI,
   `gh auth login`), asks a few questions, lets Actions open pull requests, stores their Claude
   token as a secret, and starts the first run. Each step asks yes/no first. Tell them that
   every repo they have starred so far gets reviewed, `max_per_run` per run (default 8) every
   3 hours, and that after the first run they check it is green, merge the pull request if one
   is waiting, and run `git pull`. Stop here and wait.

3. **Old v4 copy** (the doctor says "old all-in-one layout"): tell the user to run, from that
   repo's folder, `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --migrate`. It removes the
   copied code, adds the small workflow and launcher, and keeps notes, config and approvals.

4. **Data repo exists**: if `about_me` in config.yml is empty, or the user wants to change
   it, interview them, one or two short questions at a time:
   - What do they build or study? Languages and tools? What do they want from starred repos
     (save tokens, better results, ideas for projects, keeping up)?
   - Their current projects and interests, each with one line. These become the
     `relevant_to` labels on notes, so keep names short (2 to 4 words). Always keep `General`.
   - How many repos per run (default 8; 3 to 5 keeps usage low).
   - How hands-off: `auto_merge: notes` (default: PRs that approve installs wait for them),
     `low-risk` (Low-risk installs approve themselves too) or `never`.

5. Edit config.yml with the answers: `about_me` (3 to 5 lines, in their words, including what
   they are sceptical of), `contexts`, `max_per_run`, `auto_merge`. Quote strings that contain
   a colon. Keep the comments and the `labels` section. Leave `github_user` empty unless they
   want someone else's stars. Run the doctor again and fix any config problem it reports.

6. Show the finished `about_me` and `contexts`, ask if anything should change, then tell them
   to run in their data repo: `python3 star-triage.py setup --skip-config`. That commits and
   pushes the config and finishes any GitHub steps still missing.

7. When they come back, run the doctor once more and say what is left, if anything. Problems
   and fixes: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md
