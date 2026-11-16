# My stars

My GitHub stars, reviewed by [star-triage](https://github.com/thebeekeper/star-triage).

- **`tracker/`**: one note per starred repo. In Obsidian, open this folder as a vault and use
  `tracker/Star Triage.base`. Without Obsidian: `/star-triage:list` in Claude Code.
- **`config.yml`**: the star-triage mode (who approves installs), repos per run and other
  settings. Edit, commit and push.
- **`installs.json`**: every install that was reviewed, pinned, with its approval. Written by
  the workflow; approve or stop an item with the `approve` checkbox on its note, or
  `approve owner/name` / `unapprove owner/name` below.
- **`star-triage.py`**: the commands, run from this folder. Windows: `python star-triage.py sync`;
  Mac and Linux: `python3 star-triage.py sync`. Run it with no command to list them all
  (`sync --now`, `sync --dry-run`, `sync --status`, `list`, `approve`, `mode`, `schedule`,
  `pause`, `undo`, `history`, `doctor` and more).

Approved items install by themselves when Claude Code starts (in the auto modes once the
wait in `auto_wait_hours` is over). The workflow in `.github/workflows/` checks for new stars
every 3 hours (its `cron` line) and calls the star-triage Action. See or change both timers:
`python star-triage.py schedule`. Problems and fixes: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md
