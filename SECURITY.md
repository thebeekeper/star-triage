# Security

star-triage reads code written by strangers and can install it, so security reports are welcome.

## Reporting a vulnerability

Report it privately through GitHub: open the
[Security tab](https://github.com/thebeekeper/star-triage/security) of this repo and click
**Report a vulnerability** (GitHub private vulnerability reporting). Please don't open a public
issue or pull request for it.

Useful to include: what an attacker controls (a starred repo, a note, a package), what they
gain, the steps or a small example, and the star-triage version (`VERSION`).

This is a one-person project. Expect a first answer within a week. You get credit in the
changelog unless you ask not to.

## Supported versions

Only the latest release (the `v1` Action tag and the current plugin version) gets fixes.

## What counts

- A starred repo, or text in it, that gets something installed, approved or merged without the
  owner's approval, or lowers a note's risk below the scan's floor.
- An install that is not pinned to what was reviewed, or a package that passes the registry
  check without belonging to the reviewed repo.
- The reviewer (Claude) changing anything outside `tracker/` that later steps trust.
- A token or API key leaving the place it was typed into.

The design is described under "Security model" in the [README](README.md).
