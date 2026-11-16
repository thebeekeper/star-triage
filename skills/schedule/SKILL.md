---
name: schedule
description: Show or change star-triage's two timers - how often GitHub checks for new stars (every 1, 3, 6, 12 or 24 hours) and how long a reviewed tool waits before it installs by itself in the auto modes (0, 1, 6 or 24 hours). Use when the user says things like "check every hour", "how often does star-triage run", "install right away from now on", "no wait" or "change the safety delay". Also runs with /star-triage:schedule.
---

# /star-triage:schedule

Two timers, set in two places:

- **Reviews:** how often GitHub checks for new stars. It is the `cron` line in the data repo's
  `.github/workflows/star-triage.yml` (GitHub reads it only from there); `check_every_hours` in
  config.yml mirrors it. Choices: 1, 3 (default), 6, 12 or 24 hours. A check with no new stars
  stops before Claude runs, so it uses no Claude usage, about a minute of Actions time.
- **Installs:** in the auto modes, how long a reviewed tool waits before it installs by itself
  (`auto_wait_hours` in config.yml): 0 (no wait), 1 (default), 6 or 24 hours. It then installs
  the next time Claude Code starts. In manual mode nothing installs until the user approves it.

Every command is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py" <arguments>` (on Windows
`python`, or `py`). It finds the user's data repo by itself.

| The user wants | Run |
|---|---|
| to see both timers | `schedule.py` (no options: prints both, asks nothing outside a terminal) |
| "check every hour", "run less often", "once a day" | `schedule.py --check-every 1` (1, 3, 6, 12 or 24) |
| "install right away from now on", "no wait" | `schedule.py --install-wait 0` |
| a longer wait | `schedule.py --install-wait 6` (0, 1, 6 or 24; any whole number up to 168 works) |

Rules:

1. Run only what the user asked for. Don't edit config.yml or the workflow file yourself.
2. "Install right away" can mean once or always. Once (what is approved now): that is
   `/star-triage:sync now`, not this skill. From now on: `--install-wait 0`. Ask if unclear.
   High-risk and blocked items never install by themselves, whatever the wait.
3. The command commits and pushes the changed files. A workflow change can fail to push when
   the GitHub login lacks the `workflow` scope; pass on the exact commands it prints (for
   example `gh auth refresh -s workflow`, then `git push`).
4. Confirm in one or two sentences using the "Reviews:" and "Installs:" lines it printed.
