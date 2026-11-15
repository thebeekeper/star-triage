# star-triage (engine repo)

Reviews a user's GitHub stars with Claude, scans each repo for security risks, writes one
Obsidian note per repo, and installs what the user approves (Claude Code plugins, skills, MCP
servers, CLI tools), pinned to what was reviewed. Full history, decisions and roadmap:
`docs/HANDOVER.md`. Read it before any non-trivial change.

## Layout

- **This repo is the engine.** One copy of the code, used two ways:
  - `action.yml`: composite GitHub Action that a user's data repo calls as `thebeekeper/star-triage@v1`.
  - Claude Code plugin (repo root = plugin root): `.claude-plugin/plugin.json` + `marketplace.json`
    (source `"./"`), `hooks/hooks.json` (SessionStart → `scripts/sync.py --hook`), `skills/setup|sync|mode|triage`.
- **User data repo** (skeleton in `template/`): `config.yml`, `tracker/` notes, `installs.json`,
  `.claude-plugin/marketplace.json` (their reviewed plugins), `star-triage.py` launcher, tiny workflow.
- `scripts/paths.py` finds the data repo: `STAR_TRIAGE_DATA` → cwd → engine dir (old layout) →
  `~/.claude/star-triage.json` (written by setup and by the hook). Every script imports it.
- Pipeline order (see action.yml): find open PR → checkout → doctor --ci → overlay open PR's
  notes → export_stars → select_new → clone_new → OSV → scan_repo (+ trusted copy in
  `$RUNNER_TEMP/trusted`) → Claude (Read/Glob/Grep/Write/Web only) → undo changes outside
  tracker/ → apply_scan → validate_notes --changed → queue_installs → publish.

## Rules (the owner's; follow them)

- **Never make Claude a contributor.** Don't add `Co-Authored-By: Claude` or any AI trailer,
  don't commit as Claude. The owner (thebeekeper, public name "Bee") commits and pushes himself.
  Prepare changes; let him commit unless he explicitly asks you to.
- **Never ask for, read, print or store tokens or API keys.** Secrets go through `setup.py` in
  his own terminal (`claude setup-token` → `gh secret set` hidden prompt).
- **Keep the approval gates.** Default `mode: manual`; installs need a merge or an
  `approve` tick; blocked items need a typed `install`; sync never passes `--yes`. Auto modes
  (`auto`, `auto-plus`) are opt-in, never cover High-risk or blocked items, and keep the safety
  delay, the daily limit, pause, undo and history. `paths.read_mode` is the only place the
  setting (and the older `auto_merge`) is read.
- **Claude proposes, code decides.** Anything security-relevant (risk floor, install specs,
  registry checks, validation) is decided by plain code from the trusted scan copy, never from
  text Claude wrote.
- **Log design choices and user-facing problems.** Every new design choice goes in
  `docs/FEATURES.md` (marketing list); every setup/usage problem and its fix goes in
  `docs/TROUBLESHOOTING.md`.
- **No personal data in the engine.** His `config.yml`/`about_me` live only in his data repo,
  never in `template/`. Local paths, private repo names and what is installed on his computer
  go in `docs/PRIVATE-NOTES.md` (git-ignored), never in a tracked file.
- Plain, short writing in docs and messages; no marketing fluff.

## Test before handing over

```
python -m pyflakes scripts/*.py template/star-triage.py
bash tests/test_publish.sh
python tests/test_auto_mode.py              # also run by test_publish.sh
python tests/test_install_plans.py          # network
claude plugin validate .
actionlint template/.github/workflows/star-triage.yml
```
For sync.py, use an isolated `HOME`, `NPM_CONFIG_PREFIX`, `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR` (see HANDOVER).

## Releasing

1. Bump `VERSION` and `version` in `.claude-plugin/plugin.json` (users stay on a plugin version
   until it changes).
2. Add a `CHANGELOG.md` entry. Owner commits and pushes.
3. Move the Action tag: `git tag -f v1 && git push -f origin v1` (breaking changes → `v2`).
