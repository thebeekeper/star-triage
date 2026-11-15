# My stars

My GitHub stars, reviewed by [star-triage](https://github.com/thebeekeper/star-triage).

- **`tracker/`**: one note per starred repo. Open this folder in Obsidian and use
  `tracker/Star Triage.base` for the Inbox, Installs and other views.
- **`config.yml`**: about me, my projects, and how hands-off it should be.
- **`installs.json`**: every install that was reviewed, pinned, with its approval. Written by
  the workflow; approve or stop items with the `approve` checkbox on each note.
- **`star-triage.py`**: run `python3 star-triage.py sync`, `doctor` or `setup` from here
  (`python` on Windows).

The workflow in `.github/workflows/` runs every 3 hours and calls the star-triage Action.
Problems and fixes: https://github.com/thebeekeper/star-triage/blob/main/docs/TROUBLESHOOTING.md
