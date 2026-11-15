# tracker

One note per starred repo, written by the `star-triage` workflow and merged by you.

- **Obsidian:** open this folder (or the whole repo) as a vault, then open `Star Triage.base` for the Inbox, All repos, Try next, Installs and Cards views.
- **GitHub:** browse the notes here; the labels are in each note's header.
- **Automated fields:** `scan_risk` (the security scan's risk floor), `commit` (the exact commit
  that was reviewed), `install_kind` (`plugin`, `skill`, `mcp`, `cli`, `manual` or `none`) and
  `auto_install` (`queued` = approved, `available`, `blocked`, `manual` or `none`) are filled in
  by the pipeline, not by Claude. Each note also has "Security scan (automated)" and
  "Install plan (automated)" sections.
- **Approve installs:** the `approve` checkbox. Ticked items install on your computer by
  themselves (see the main README); untick one and it is removed again. The **Installs** view
  in `Star Triage.base` lists everything that can be installed.
- **Triage:** change `status` (Inbox → Reviewing → Try next / Using / Later / Skipped). Later runs
  never overwrite your notes, unless you ask for a re-review (the `retriage` option), which keeps
  your status and approve choice.
- **Adding one by hand:** copy `_template.md`, fill it in, set `source: "Manual"` and change its tag to `star-triage`.
