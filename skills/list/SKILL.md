---
name: list
description: Show the user's star-triage reviews without Obsidian (repo, kind, verdict, risk, install status, when you'd use it), one review in full, or approve / unapprove an install. Use when the user asks things like "what did star-triage review", "what is waiting for me", "tell me about ponytail", "approve ponytail" or "don't install ponytail". Also runs with /star-triage:list.
---

# /star-triage:list

Every command is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reviews.py" <arguments>` (on Windows
`python`, or `py`). It finds the user's data repo by itself.

| The user wants | Run |
|---|---|
| their reviews ("what did star-triage review", /star-triage:list) | `list` |
| what needs them ("what is waiting for me") | `list --waiting` |
| one repo in full ("tell me about ponytail") | `list --repo owner/name` (a bare name works when it is unique) |
| "approve ponytail", "install ponytail" | `approve owner/name` |
| "don't install ponytail", "unapprove ponytail" | `unapprove owner/name` |

Rules:

1. Show the list as a Markdown table with the same columns (Repo, Kind, Verdict, Risk, Status,
   When you'd use it), newest first, as printed. For `--repo`, keep the security findings and
   risks in full; don't soften them.
2. Run `approve` only when the user asked to approve or install that repo in this conversation,
   never on your own initiative and never for several repos at once unless they named them all.
   If more than one repo matches a bare name, ask which one.
3. Before approving something whose status is `blocked`, say why the scan blocked it (from
   `list --repo`). approve only ticks the box: a blocked item still installs only when the user
   runs `python star-triage.py sync` in their own terminal and types `install`. Tell them so.
   Never type or pass `install` for them.
4. Pass on the last lines approve printed: when it installs, or why it won't by itself (High
   risk in an auto mode, blocked, paused). If it says "install now", the user can run
   /star-triage:sync now.
5. Don't edit notes, `installs.json` or `config.yml` yourself; these commands do the one edit
   needed.
