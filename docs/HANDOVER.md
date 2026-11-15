# star-triage: handover

Everything built so far, why it is the way it is, where things stand, and what's next.
Written for continuing development in Claude Code. Start with "Rules" in `.claude/CLAUDE.md`.

## 1. What it's for

The owner (GitHub `thebeekeper`, public name "Bee") kept finding interesting repos (Claude Code
skills, plugins, MCP servers, tools) and losing track of them. The project succeeds only if it
does **both**:

1. **Grab every starred repo and save it somewhere useful**: a researched, security-checked note
   per repo in an Obsidian vault.
2. **Install whatever is needed to make the good ones run**, automatically, safely.

It is also meant to become a public open-source project, so every design choice is logged in
`docs/FEATURES.md` (for marketing) and every problem a user can hit in
`docs/TROUBLESHOOTING.md`.

## 2. How it got here (Nov 2026)

1. **Review.** It started as a one-off review of a batch of skills and repos: install, try or
   skip, for better Claude Code results and fewer tokens.
2. **Tracker.** First a Notion database, then **Obsidian only**: Markdown notes with YAML
   properties + an Obsidian Bases view (`tracker/Star Triage.base`).
3. **v1, automation.** GitHub Action polls the owner's stars (GitHub has no "user starred a repo"
   event), Claude (via `anthropics/claude-code-action@v1`) researches new ones and opens a PR
   with one note each. Made into a template repo so others can use it.
4. **v2, security + plugin installs.** Plain-code scanner (`scan_repo.py`) + OSV, a risk floor
   Claude can't lower, a trusted copy of scan results outside Claude's reach, a revert of
   anything Claude changed outside `tracker/`, a strict validator. Approved Claude Code plugins
   go into a personal marketplace pinned to the reviewed commit; `sync.py` installs them.
   Checks every 3 hours.
5. **v3, less friction.** Notes-only PRs merge themselves; an open PR receives later runs'
   notes (rolling PR) instead of blocking runs; `setup.py` (guided setup, token never touches an
   AI) and `doctor.py` (checks + exact fixes), CI preflight.
6. **v4, installs everything it can.** The owner pointed out plugins-only installs meant
   nothing he wanted could install. Now: plugins, skills, MCP servers (hosted URL or npm/PyPI
   package) and CLI tools (npm/PyPI, optional README-verified setup step, PyPI extras). Claude
   only *proposes* an install method; `queue_installs.py` verifies it (package must link back
   to the repo by GitHub ID; URL/setup command must appear in the repo) and pins it.
   Approval: merge, or the `approve` checkbox on a note. Automatic install via a SessionStart
   hook. `retriage` input to re-review old notes.
   - v4.1: the marketplace lists every reviewed plugin (blocked ones too) so a user-confirmed
     blocked plugin can install; prompt retuned so clear wins get "Install" again (Claude had
     become too cautious once Install meant a real install).
7. **v5, the engine (this version).** Code moved out of every user's copy: this repo is a
   composite **GitHub Action** plus a **Claude Code plugin** sharing one `scripts/` folder. A
   user's data repo holds only data, a launcher and a 20-line workflow. `setup.py` can create
   the data repo, link one, or migrate a v4 copy (`--migrate`).

## 3. Architecture

```
GitHub (every 3 h, in the user's data repo)            The user's computer
-------------------------------------------            -------------------
workflow -> thebeekeper/star-triage@v1 (action.yml)     Claude Code starts
  export stars, pick new (+ retriage)                    -> plugin SessionStart hook
  clone read-only (commit recorded), OSV, scan_repo         sync.py --hook (0.1 s): git pull,
  trusted copy of scan -> $RUNNER_TEMP/trusted              plan from installs.json + approve
  Claude: writes notes + install proposals only             ticks, start background sync
  revert everything outside tracker/                     -> sync.py --background installs:
  apply_scan (risk floor), validate_notes                   plugin  claude plugin install (pinned sha)
  queue_installs (verify + pin + approve/block)             skill   tarball @sha -> ~/.claude/skills
  publish: PR, rolling PR, auto-merge per auto_merge        mcp     claude mcp add (url / npx / uvx)
                                                            cli     npm -g / uv tool (+ setup step)
data repo: config.yml, tracker/*.md, installs.json,      next start: "installed X" message
           .claude-plugin/marketplace.json (my-stars)   python star-triage.py sync|doctor|setup
```

Trust boundaries: the starred repo's content is hostile input (Claude is told so; nothing from
it runs during review). Claude's output is untrusted: it may only write notes and proposals,
everything else is reverted, and every decision is re-derived from the trusted scan copy and
registries. The merge (or a tick) is the human approval.

## 4. Key decisions and why

- **Poll every 3 h** (cron `17 */3 * * *`): no star event exists; runs with no new stars stop
  before Claude (no usage), ~240 Actions min/month private.
- **Obsidian over Notion**: plain Markdown in git, works offline, Bases gives table views.
- **Merge = approval; human gate kept by default.** `auto_merge: notes` (default), `low-risk`,
  `never`. The owner explicitly agreed to keep the human merge step; `low-risk` is opt-in.
- **Plugins before skills, then everything**: v2 started with plugins (official pinned mechanism),
  v4 added the rest because goal 2 failed without them.
- **Pin everything**: plugin/skill → reviewed commit; packages → version seen at review.
- **Registry link check by GitHub ID**, not name (handles renames/transfers; e.g. ccusage moved
  from ryoppippi/ccusage to ccusage/ccusage).
- **Setup commands must appear in README code blocks** and can't be shells/interpreters/downloaders.
- **API keys are never moved** by sync; MCP servers needing a key get the command to run.
- **Blocked ≠ forbidden**: never installed in the background; a typed `install` allows it.
- **Hook in a plugin, not settings.json**: no editing user settings; updates with the plugin.
  Shell form, so it can try `python3`, then `python`, then `py` (macOS and Linux often have
  no `python`). `run_hook()` always exits 0, so a later one runs only when the earlier is missing.
- **Action + plugin instead of a template full of scripts**: copies never got fixes.
- **Data repo found via `paths.py`** (env → cwd → engine → link file) so the same scripts work
  in CI, from the plugin cache, from a local plugin folder and from the old layout.
- **Claude never a contributor**; owner commits. Workflow commits are by github-actions[bot].

## 5. Current state (16 Nov 2026)

- **Engine** `github.com/thebeekeper/star-triage`, v5.1.0, Action tag `v1`. Still private on
  this date; while private, only the owner's repos can call the Action.
- The owner's own data repo runs on v5 (Action + plugin) and is the day-to-day test.
- Audit of Nov 2026: kept privately (git-ignored `docs/AUDIT-*.md`) until its open findings are fixed.
- Owner-only details (local paths, the private data repo, what is installed where) are in
  `docs/PRIVATE-NOTES.md`, which is git-ignored. Don't copy them back into tracked files.

## 6. Testing

See `tests/README.md`. Manual sync test recipe (never touches the real machine):

```
export HOME=/tmp/h NPM_CONFIG_PREFIX=/tmp/npmg UV_TOOL_DIR=/tmp/uvt UV_TOOL_BIN_DIR=/tmp/uvt/bin
export PATH=/tmp/npmg/bin:/tmp/uvt/bin:$PATH
STAR_TRIAGE_DATA=<data repo copy> python scripts/sync.py --dry-run --no-pull
```
Setup was tested with a fake `gh` that creates a local bare repo (keep the GitHub URL as the
fetch URL and set a local `--push` URL, or `detect_repo` sees a local path). Piped stdin can't
drive the second half of setup after it re-executes itself (the parent buffers input); test that
half separately with `STAR_TRIAGE_DATA` set.

## 7. Gotchas already hit (and fixed or documented)

- Wrong commit email linked commits to a stranger's account (`12345678+...`); setup now uses
  `<id>+<login>@users.noreply.github.com` and the doctor checks the ID.
- Windows: `claude` not on PATH after install (`%USERPROFILE%\.local\bin`), PyYAML missing,
  `npm` shims blocked by PowerShell execution policy, `uv`/`gh` need a new terminal.
- Bases needs Obsidian 1.9+; the data repo must be opened as a vault.
- Scheduled runs have no `github.event.repository` → BASE falls back to `github.ref_name`.
- `git status --porcelain` + `.strip()` ate the first path character → use ls-files/diff.
- Scanner URL cap too small for big docs repos (context7) → 5000, query strings dropped.
- Not-cloned scan result lacked `install_blockers` → would have queued unreviewed repos.
- PyPI MCP servers often need extras (`scrapling[ai]`) → `extras` + `command` fields.
- Blocked plugins were left out of the marketplace → user-confirmed installs failed.
- Claude stopped giving "Install" once it meant a real install → prompt defines Install per owner.
- The device bridge used during development couldn't write `.github/workflows/` or `.claude/`
  (files went via `_incoming/`); not an issue inside Claude Code.

## 8. Next steps (roadmap)

1. ~~Ship v5~~ Done 15 Nov 2026: engine pushed and tagged `v1`, the owner's data repo
   migrated, plugin installed, doctor all green, a real run reviewed a new star through the
   Action and merged its PR. 5.1.0 (16 Nov 2026): the audit fixes, see `CHANGELOG.md`.
2. CI for the engine repo itself: run `tests/` + pyflakes + `claude plugin validate --strict`
   + actionlint on every push.
3. Polish before going public: README GIF, issue templates, `CHANGELOG.md` upkeep, make the
   repo public, turn on private vulnerability reporting (`SECURITY.md` points at it), consider
   submitting the plugin to Anthropic's directory. The old commits still hold the personal
   details removed in 5.1.0; rewrite history or start the public repo from a fresh commit.
4. Product ideas: per-kind `auto_merge` (e.g. skills only), `--prune` UX for items removed from
   installs.json, an MCP-key prompt flow that stays out of Claude's reach, star-list-based
   categories, retriage of everything (`all`), optional Notion export, dashboard of usage.
