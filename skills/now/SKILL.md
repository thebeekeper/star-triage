---
name: now
description: Review the stars the user just added and install what is safe, right now, in one go - starts the star-triage run on GitHub, waits for it, asks before merging a review PR, then installs what is approved without the safety delay. Use when the user says things like "check my stars now", "review my new stars now", "install my new stars now" or "run star-triage now". Also runs with /star-triage:now.
---

# /star-triage:now

All the work is done by `${CLAUDE_PLUGIN_ROOT}/scripts/now.py` (run it with `python3`; on
Windows `python`, or `py`). It finds the user's data repo by itself.

The review still happens on GitHub (security scan, then Claude, in the workflow). This only
skips the waiting: the schedule, and the safety delay of the auto modes. The daily limit, pause,
High-risk and blocked rules stay.

1. Run `now.py` once. Pass `--max N` only if the user gave a number of repos, and
   `--retriage owner/name,owner/name` only if they asked to review named repos again. It can
   take up to 20 minutes: run it with the Bash tool's `run_in_background`, and read its output
   when it finishes. Tell the user once that the run started (and its URL when printed); don't
   poll or re-run it while it works.
2. If it stops with a fix (gh not installed or logged in, workflow not registered, run failed),
   pass on that fix and the run URL as printed, and stop. Don't try the fix yourself unless the
   user asks; never run `gh auth login` or anything that handles a token.
3. If it ends with `ANSWER NEEDED: merge review PR #N now?` (exit code 3), show the PR URL and
   the installs it lists, then ask with AskUserQuestion: "Merge review PR #N now?" with the
   options "Merge it" and "Keep it open". Then run exactly one of the two commands it printed,
   with `now.py` in place of `star-triage.py now`:
   - "Merge it": `now.py --merge N --head SHA` (the SHA it printed, so a PR that changed since
     is not merged)
   - "Keep it open", or any other answer: `now.py --no-merge N`
   Never pass `--merge` unless the user picked "Merge it" in this conversation, and never merge
   the PR any other way.
4. Finish with its Summary in a few lines: how many repos were reviewed, what was installed,
   what waits for the user and why (High risk, blocked, not ticked, Skip, manual install, an open
   review PR). In manual mode, say plainly that only ticked items and approved Install verdicts
   install, and name the notes that wait for a tick.
5. Blocked items need the user to type `install` in their own terminal: tell them to run
   `python star-triage.py sync` in their data repo. Never type or pass `install` for them, and
   never add `--yes`.
6. If the output says "Restart Claude Code to load new plugins and MCP servers", say so last.
7. Never edit notes, `installs.json` or `config.yml`, and never ask for or handle tokens or
   API keys. If something looks wrong, point to the Troubleshooting guide:
   https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md
