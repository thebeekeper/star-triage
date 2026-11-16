---
name: mode
description: Show or change who approves star-triage installs (manual, auto or auto-plus), and pause, resume, undo or list automatic installs. Use when the user says things like "turn on auto mode", "stop installing things", "pause star-triage", "undo what star-triage installed" or "what did star-triage install". Also runs with /star-triage:mode.
---

# /star-triage:mode

One setting decides who approves installs:

- `manual`: I approve every install (the default)
- `auto`: Install safe things by themselves (Low risk only)
- `auto-plus`: Install safe and medium-risk things by themselves (Low and Medium)

High-risk and blocked items are never installed automatically, in any mode.

Every command below is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<script>" <arguments>` (on Windows
`python`, or `py`). The scripts find the user's data repo by themselves.

| The user wants | Run |
|---|---|
| to see the mode | `mode.py` (prints the mode and the three choices) |
| "turn on auto mode", "install safe things for me" | `mode.py auto` |
| medium-risk things installed too | `mode.py auto-plus` |
| "stop installing things", "go back to asking me" | `mode.py manual` |
| a break without changing the mode | `sync.py --pause`, later `sync.py --resume` |
| to see what was installed automatically | `sync.py --history` |
| the last automatic batch removed | tell them to run `python3 star-triage.py undo` in their own terminal, in their data repo: it asks y/n, which you cannot answer for them |

Rules:

1. Say "star-triage mode" in replies, so it isn't confused with Claude Code's own auto mode
   (a permission mode). If "auto mode" could mean Claude Code's setting, ask which they mean.
2. Run exactly one of these commands for what the user asked. Do not edit `config.yml`, notes or
   `installs.json` yourself, and do not add other flags.
3. If the user's words don't say which mode ("change the mode"), run `mode.py`, show the three
   choices in the words above, and ask which one. Never pick `auto-plus` unless they asked for
   medium-risk installs.
4. `mode.py <mode>` commits `config.yml` as "Switch star-triage to <mode>" and pushes it. If the
   push fails it prints the exact commands to run; pass them on word for word.
5. Confirm in one sentence, using the last line the command printed.
6. "Stop installing things" is ambiguous. If they mean for good, use `mode.py manual`. If they
   mean for now, use `sync.py --pause`. Ask if it is not clear.
