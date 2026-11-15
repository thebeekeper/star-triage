---
name: sync
description: Install, update or remove what the user approved through star-triage (plugins, skills, MCP servers, command-line tools), or show what is installed. Run manually with /star-triage:sync.
disable-model-invocation: true
---

# /star-triage:sync

Brings this computer in line with the user's approvals: items approved by merged star-triage
PRs (in `installs.json` of their data repo, each pinned to a reviewed commit or version) and
items the user ticked `approve` on in Obsidian. The plugin already does this in the background
whenever Claude Code starts; this skill is for doing it now, or checking what happened.

All the work is done by `${CLAUDE_PLUGIN_ROOT}/scripts/sync.py`. It finds the data repo by
itself (the current folder if it is one, otherwise the one linked by setup).

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" --status` (on Windows `python`, or `py`;
   use the one that works in every command below) and summarise it. If it says no
   data repo was found, run the /star-triage:setup skill's steps instead.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" --dry-run --no-pull` and show the plan:
   what will be installed, updated or removed, and of which kind. If nothing would change, say so.
3. Ask the user to confirm. Only after a clear yes, run
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py"`.
4. Never add `--yes`, `-y` or `--accept-command`, never install anything that is not in
   `installs.json`, and never edit `installs.json` or `.claude-plugin/marketplace.json`.
   Never ask for or handle API keys: if an MCP server needs one, show the user the command
   sync.py printed so they can add it themselves.
5. Blocked items need the user to type `install` at a prompt, and some tools ask questions while
   installing. When that happens, tell the user to run, in their own terminal from their data
   repo: `python3 star-triage.py sync`.
6. If a step fails, show the last lines of `.star-triage/sync.log` in the data repo and point to
   the Troubleshooting guide: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md
7. To undo an install: `sync.py --uninstall NAME` (it also unticks the note).
8. Finish by telling the user to restart Claude Code if plugins or MCP servers changed.
   Skills work right away.
