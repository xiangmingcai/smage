# FAQ and troubleshooting

## Where is the log file?

Every project writes a diagnostic log to:

```
<project>.smage\log\smage_<date>_<time>_p<pid>.log
```

The newest file is the current session. It records actions, render timings
and full tracebacks. **Attach the newest log when reporting a problem.**

Global settings and the self-check report live in `%APPDATA%\Smage\`.

## Windows says "Windows protected your PC"

The build is not code-signed yet. Click **More info → Run anyway**. See
[Installation](../getting-started/installation.md).

## A module is locked / greyed out

A module with saved output locks the modules above it. Unlock it (or remove
its output) before editing upstream settings. The lock badge is shown in the
workflow tree.

## Downstream modules do not see my changes

Check that the upstream module was **saved**. Downstream modules only see the
saved state; the Save button is enabled while there are unsaved changes.

## Gate counts do not change when I move a gate

Counts refresh when the drag is released. Until then only the gate label
moves. After release the tree updates within a fraction of a second. If it
does not update at all, check the log for `gating.stats_error` and report it.

## Counts are percentages of what?

A gate's percentage divides its count by its **parent** count (or by the whole
file for top-level gates). Boolean gates use their own sources.

## "Update statistics with full data" changes nothing

If the data file is smaller than the sampling cap, the default statistics
already use every row, so the button recomputes exactly the same numbers. The
button's tooltip says when the sample covers the whole file.

## The application is slow with large images

Working with very large images or millions of events genuinely takes time.
These operations are CPU-, memory- and disk-bound, and the wall-clock time
depends heavily on the computer: a faster CPU, more RAM and an SSD make a
noticeable difference. There is no setting that removes the work — expect the
first run of an expensive step (Unmix, Illumination Correction, Segmentation,
clustering, dimensionality reduction) to take a while.

- Close unused module tabs; each open module keeps caches.
- The first Unmix/Illumination estimate is expensive; later modules reuse it.

## How do I move a project to another computer?

Copy the whole `<name>.smage` folder. Paths inside the project are stored
relative to it (generated files and masks live under `Module/`).

## Where are my global settings?

`%APPDATA%\Smage\settings.json` and `extensions.json`. They are not part of a
project, so copying a project does not copy them.

## Reporting a bug

Open an issue with the version, Windows version, reproduction steps and the
log. Security problems: see [SECURITY](https://github.com/xiangmingcai/smage/blob/main/SECURITY.md)
and report them privately, not as a public issue.
