# FAQ and troubleshooting

## Where is the log file?

Every project writes a diagnostic log to:

```
<project>.smage\log\smage_<date>_<time>_p<pid>.log
```

The newest file is the current session. It records actions, render timings
and full tracebacks. **Attach the newest log when reporting a problem.**

Global settings and the self-check report live in `%APPDATA%\Smage\`.

## Windows says "Windows protected your PC" / the app won't run

The build is not code-signed yet. If **More info → Run anyway** is available,
use it. If that option is missing, see
[Windows protected your PC](windows-protected-your-pc.md) for unblocking the
file, Smart App Control and managed-device policy.

## A module is locked / greyed out

A module with saved output locks the modules above it. Unlock it (or remove
its output) before editing upstream settings. The lock badge is shown in the
workflow tree.

## Downstream modules do not see my changes

Check that the upstream module was **saved**. Downstream modules only see the
saved state; the Save button is enabled while there are unsaved changes.

## Can I use Smage for clinical decisions?

No. Smage is research software, not a medical device, and it must not be used
for clinical diagnosis, treatment selection or any other clinical decision.
It comes without warranty, and you are responsible for the analysis and its
results. See
[DISCLAIMER.md](https://github.com/xiangmingcai/smage/blob/main/DISCLAIMER.md).

## A converted Region is not visible downstream

Check two things in the Region tree:

1. the Region row shows **Converted** in the **Status** column (press
   **Convert as channel** if it says *Not converted* or *Needs update*);
2. **Downstream visible** is ticked.

If the boundary was edited after converting, the status becomes *Needs
update* but the old mask stays in use until you convert again. When the
selected channels change, the converted mask columns may not match them; the
Region Size module then shows **NA**. See [Region](../modules/region.md).

## How do I reuse an Unmix matrix in another module?

In the source module press **Copy matrix to clipboard** (in the matrix
settings), then in the target module choose **Signature source → Manual
input** and press **Paste matrix**. The channels must match the target's
Spectrum Extraction selection; the pasted values are written with one
decimal, so results are close to, but not bit-identical with, the source.
See [Unmix — Matrix Unmixing](../modules/unmix-matrix.md).

## How do I save a heatmap or a plot as a file?

Right-click the heatmap or plot and choose **Save as…**. PDF keeps vector
shapes and editable text; PNG/JPEG are rendered at three times the screen
resolution. In Figure the same action is in the plot card's right-click menu.

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
