# The claude.ai version

The desk is also published as a claude.ai artifact: "SEPA + CAN SLIM Desk", https://claude.ai/artifact/Wm9kXCh41spFGGGeFXGWRf (slug `f10323be-105d-41d9-a380-473becd9ab62`).

- **Page:** `../static/index.html` is the published page, byte for byte. Edit it here, test it locally (`python3 desk.py serve`), then republish it to the same artifact from a claude.ai/Cowork conversation ("republish static/index.html to my SEPA desk artifact"). It must stay self-contained: inline CSS and JS, no external scripts.
- **Capabilities:** `capabilities.json` lists the runtime capabilities the published page declares (database, the Google Drive connector tools, and Claude Code Remote `fire_trigger` for the news button).
- **Scheduled tasks on claude.ai** (they keep running there until you turn them off):
  - "SEPA Desk daily update" (`trig_018CLJZHef975KciQuwHQ6Ac`), 10:15 NZ time Tuesday–Saturday. Prompt: `scheduled-tasks/daily-update.md`.
  - "SEPA Desk news scan" (`trig_01PwdUWcj1YLhVyL5JeEiap7`), started by the Find news button. Prompt: `scheduled-tasks/news-scan.md`. It needs "Automatically approve" turned on in its settings.
- **Data:** the claude.ai database and the local `data/desk.sqlite` are separate. `data/seed/` holds the claude.ai data as of 2026-11-05. To move local changes back, export (`python3 desk.py export out/`) and ask Claude in claude.ai to write those documents to the artifact database.

If you only use the local version from now on, turn off both scheduled tasks on claude.ai so they stop running.
