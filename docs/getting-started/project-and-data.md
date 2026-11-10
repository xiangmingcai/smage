# Projects and data

A Smage project is a folder named `<name>.smage`. It contains the imported
data, every module's settings, generated files and the diagnostic log — so an
analysis is one self-contained folder.

## Folder layout

```
MyExperiment.smage\
├── project.json        # manifest: metadata, samples, the workflow (task tree)
├── ui_state.json       # window/UI preferences (tree state, …)
├── data\
│   ├── case.parquet    # one normalized table per imported file
│   └── columns.json    # column index used by the file pickers
├── Module\
│   ├── gating-abc123\  # per-module data (gates.json, masks, feature tables)
│   └── segmentation-…\
└── log\
    └── smage_20260101_120000_p1234.log
```

### What each module stores

| Module | Stored under `Module/<task-id>/` |
|---|---|
| Data Import | The normalized tables in `data/` |
| Gating | `gates.json` (files, gates, per-file overrides) |
| Segmentation | Mask files (one per image) |
| Illumination Correction | Estimated correction masks |
| Feature Down / Feature Up / Gate2Col | Generated Parquet tables |
| Everything else | Only settings, inside `project.json` |

## Paths

Paths inside a project are stored **relative to the project folder**, so a
project can be copied or moved to another disk or computer without breaking.
Do not rename the `data\` or `Module\` folders by hand.

## Logs

Every session writes a log to `<project>.smage\log\`. The newest file is the
current session. Logs include actions, render timings and full tracebacks —
attach the newest one when reporting a problem (see
[FAQ](../reference/faq.md)).

## Global settings

These live outside the project, in `%APPDATA%\Smage\`:

- `settings.json` — appearance and plot defaults,
- `extensions.json` — extension environments (FlowSOM, PARC, Cellpose, …).

They are not copied with a project. On a new computer, the extension
environment has to be created again from the **Extension Manager**.

## Moving and backing up

1. Close Smage (so the project is fully written).
2. Copy the whole `<name>.smage` folder.
3. Open it on the other computer with `File → Open Project…`.

There is no database and no lock file that survives a normal close, so a
folder copy is a complete backup.

## Privacy

Smage processes everything locally. It does not upload data, and there is no
account or telemetry. The only network access happens when you install
extensions or download segmentation models from the Extension Manager, or when
you open links in a browser.
