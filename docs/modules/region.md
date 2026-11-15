# Region

Detects and manages **image regions** (for example tumour, damage or empty
areas) and, when converted, exposes each Region to downstream modules as a
**0/1 channel**. It works only with image files.

A Region can consist of several **sub-regions** on one file, and the same
Region may have different sub-regions on different files. Each sub-region is
a set of `in` (filled) and `out` (hole) rings. Presence is per-file: a file
may not contain a Region at all, or may contain it with no sub-regions (an
empty Region).

A new Region module starts with **no image files selected**. Use
**Select files…** to choose them; the preview and detection stay empty until
you do.

## The page

| Area | Contents |
|---|---|
| Left top | Image files used by this module. |
| Left middle | Internal **detection gates** (rect / ellipse / polygon / boolean). |
| Left bottom | The **Region tree**: Region → Sample → Sub-region. Region rows also have **Convert as channel**, **Status** and **Downstream visible** columns. |
| Middle | Image with **Gating tools** and **Region tools**; the detection panel is below it. |
| Right top | **Image settings**: file, channel, levels, display toggles and font sizes. |
| Right middle | **Region setting**: drawing style of the selected Region. |
| Right bottom | **Log**: detection progress and results. |

## Internal detection gates

The gate tree is local to this module and is never visible downstream. A gate
defines **where** detection is allowed to run: the selected gate and its
parents are combined, and detection results are clipped to that area. Select
**Root** to detect over the whole image.

Draw a gate with the **Gating tools** — Rectangle, Ellipse or Polygon — while
a gate (or Root) is selected in the tree; the new gate becomes its child.
Double-click a gate to rename it; the context menu deletes it. Gates with
children must have their children removed first, and a gate used by a boolean
gate cannot be deleted until that boolean gate is deleted.

### Region tools

The two **Region tools** edit the Region tree rather than the detection gate
tree:

- **Region in** adds a filled polygon. It is available when a Region, Sample
  or Sub-region is selected. With a Region or Sample selected, the polygon
  starts a new Sub-region; with a Sub-region selected, it is added to that
  Sub-region.
- **Region out** removes the drawn polygon from the selected Sub-region. It
  requires a selected Sub-region. The removal can split one Sub-region into
  several pieces or carve holes; this is allowed because a Sub-region is a
  set of closed areas.

A Sub-region stores `in` rings (filled areas) and `out` rings (holes). The
final area is the **even-odd fill** of all its rings: an `in` polygon drawn
inside an existing hole becomes an island without deleting the surrounding
`out` ring. Every edit is converted to a pixel mask, then re-traced into
`in/out` rings and anchors, so dragging an anchor (after **Edit boundary…**)
can also create a new hole.

There is no Cancel button: press **Esc**, click the active tool again, or
choose another tool to cancel drawing.

### Boolean gates

The gate context menu has **Boolean gating → NOT / AND / OR**. A boolean gate
combines other internal gates and has no shape of its own:

- **NOT** creates a sibling of the selected gate and selects the points
  outside it.
- **AND / OR** combine the selected gate with one or more sibling gates at the
  same tree level.

Boolean gates can be nested and can combine other boolean gates. Selecting
one as the detection gate detects inside the combined area; for example
`NOT (A OR B)` detects outside both gates.

### Per-file geometry

By default a gate has one position and size for every file. Turn on
**Per-file gate** in the gate context menu to adjust the gate separately for
each file: while the module shows a file, moving or resizing the gate stores
that shape only for the current file. Files without an adjustment keep the
shared shape, which is also shown when per-file mode is first enabled.
Per-file gates are shown in italics in the tree.

**Apply one file to all…** copies the shape of the chosen file to every
selected file and turns per-file mode off. The gate type never changes; a
boolean gate uses per-file geometry through its source gates.

## Detection methods

### Signal Expansion

Each parameter in the Detect Region panel has a short hint underneath it
explaining what it changes.

1. Read the selected channel through the pipeline.
2. Gaussian smoothing.
3. Threshold, chosen by **Threshold mode**:
   **Auto (Otsu)** computes the cut-off from this file's own smoothed pixels
   inside the detection gate; **Percentile** uses this file's own percentile
   (30% makes the 30th percentile the cut-off, so about 70% of that file's
   pixels become signal); **Manual** uses one fixed value for every file.
   Auto and Percentile are always evaluated per file, and the threshold
   actually used is written to the log for each file.
4. Morphological expansion joins nearby signal.
5. Fill holes. **Max hole area** limits this to holes up to that many
   pixels; 0 fills every enclosed hole. Larger lumens stay open.
6. Clip to the detection gate.
7. Remove components below **Min area**.
8. Trace each component's outline and simplify it to a polygon
   (**Tolerance**, in pixels). Enclosed holes stay empty (stored as `out`
   rings), so a ring of cells does not become a filled disc over a lumen.
   A component that touches the image border is traced with a one-pixel
   empty frame, so its outer contour stays closed and its holes keep their
   `out` role.

If the final foreground covers 80% or more of the image, the log prints a
warning. That usually means the threshold is below the background noise or
**Expand** is too large, and the parameters or the channel should be changed.

### SAM 3

SAM 3 runs in a separate Python environment configured in
**Extension Manager → SAM 3**; Smage does not bundle torch or the model
weights. The user accepts the model license and downloads `facebook/sam3`
through the extension. The selected channel is normalized to an 8-bit RGB
image and passed to SAM's automatic mask generator; the resulting masks are
clipped to the detection gate and converted to sub-regions with the same
minimum-area and tolerance settings.

Use **Manage SAM…** in the detection panel to configure it. SAM parameters:
points per side, points per batch, predicted IoU threshold, stability
threshold, crop layers and minimum mask area.

SAM 3 is released by Meta under the **SAM License** (2025-11-19), not an
open-source license. It allows research and commercial use, but forbids
military, nuclear, espionage and weapons use, requires a copy of the license
when the materials are redistributed, and requires research publications to
acknowledge SAM. Smage never bundles or redistributes the SAM code or
weights, so the license is accepted by the user who downloads them and does
not change Smage's GPL-3.0 terms.

Detection runs in the background and reports progress per file in the
**Log** panel at the bottom right. Each line has a timestamp, and every
detection stage (read channel, detection gate, smoothing, threshold,
binarisation, expansion, fill holes, component filter, contours, total)
reports its own elapsed time, so slow stages are easy to spot. The threshold
stage and each file's result line show the value that was actually used, so
files with different Auto or Percentile cut-offs can be compared. The lines
use the file names shown in the project, not internal parquet ids. The log is
saved with the module and is restored when the page reopens; a new run
appends to it instead of clearing the history. Every run creates a **new
Region** named `Region 1`, `Region 2`, …; it never replaces or merges an
existing Region. Files with no detected region parts are kept as empty
Samples.

## Region tree

- **Show** controls only the image overlay, never what downstream modules see.
  Clicking the Show checkbox of a Sample or Sub-region also switches the
  image to that file; the overlay always contains only the Regions that have
  a Sample on the currently displayed file.
- Region and Sub-region can be renamed with **Rename** in the context menu
  (or by double-clicking the name); Sample cannot be renamed. A Region name
  that is already a channel name in any module's data files is refused.
- The image itself has no right-click menu; all editing is done with the
  toolbar tools and the Region tree.
- **Add New Region** creates an empty Region.
- **Add Sample…** adds files that are not yet in that Region.
- **Add Sub-region** is available only while the image shows that Sample;
  it uses the Region in tool, so draw one polygon and it becomes a new
  Sub-region.
- Region, Sample and Sub-region can be copied and pasted as a whole branch.
  Paste a copied **Sample** into another Region to copy all of its
  Sub-regions (with new ids). If that Region already contains the Sample,
  Smage refuses the paste and says so instead of creating a duplicate.
  A pasted sub-region can target a Sample in another file; its in/out rings
  are copied as-is.
- Selecting a Sub-region — in the tree or by clicking it in the image —
  highlights its boundary and selects it in the tree. It does not create
  anchor handles. Hold **Shift** for a range or **Ctrl** to toggle several
  rows: every selected Sub-region on the displayed file is highlighted
  together, and **Copy Branch** / **Delete** in the context menu then apply
  to the whole selection. A copied group pastes into a Sample as separate
  Sub-regions with new ids.
- Right-click the Sub-region and choose **Edit boundary…** to show every
  `in/out` ring as an editable polygon. Dragging an anchor re-traces the
  Sub-region through a pixel mask, so a new enclosed hole becomes an `out`
  ring automatically. Press **Esc** or select another item to hide the
  anchors. When the Sub-region has more than 100 anchors, Smage first warns
  with the count and asks for confirmation, because displaying that many
  handles can freeze or crash the window.

## Drawing style

Each Region has its own fill colour, fill opacity (0% = no fill), edge colour
and edge width. All sub-regions of a Region share that style; the selected
Sub-region is outlined in amber so it can be found in the image. Styles
affect the image only.

## Converting a Region to a channel

Each Region row has three extra columns after **Show**:

| Column | Meaning |
|---|---|
| **Convert as channel** | Writes the Region to a 0/1 channel on every file that has a Sample. |
| **Status** | `Not converted`, `Converting n/m`, `Converted`, `Partial`, `Needs update`, `Failed` or `Name conflict`. |
| **Downstream visible** | Off by default. It becomes available only when every file with a Sample has a valid mask. |

Pressing **Convert** saves the current Region state, then writes one
`[x, y, mask]` parquet per file plus a mask index pyramid
(`<name>.parquet.imgidx/`). Pixels inside the Region are 1; all other pixels
are 0. A Sample with no sub-regions is written as an all-zero mask (area 0).
Files without a Sample are not converted, so they are NA downstream.

**Convert** and **Downstream visible** are disabled while the module is
locked (or opened as a locked draft). Progress and timings are written to the
**Log** panel. The mask and the Downstream visible state are kept when a
boundary is edited afterwards: the
Status changes to `Needs update` and the old mask remains visible until
**Reconvert** is pressed. Deleting a Region (or a Sample) hides its channel
immediately; the mask files are removed when the module is saved (closing
without saving keeps them).

Region names are channel names. Renaming is refused when the new name is
already used by another Region or by any channel in the data files selected
by any module. **Convert** performs the same check again.

## What downstream modules see

Downstream modules see a converted Region only as a **normal 0/1 channel**,
and only when **Downstream visible** is ticked. Regions are no longer exposed
as gates, filters, image overlays, Gate2Col gates or Heatmap annotations, so
a Region that is not converted is invisible below this module.

A converted channel can be used anywhere a channel is accepted: as a Figure
image layer or scatter axis, a Gating axis, a Heatmap/Export column, and in
[Region Size](region-size.md). A file without a converted mask has no such
channel, so downstream reports it as unavailable/NA. A Sample with no
sub-regions has an all-zero channel and therefore area 0.

## Saving

Press **Save** to write the gates (including per-file shapes), the Region
tree and the detection settings to `Module/<id>/regions.json`. **Convert**
and **Downstream visible** also save immediately, because their mask files
are products that downstream modules read. Unsaved geometric edits stay in
the editor until Save; the Status column still notices them and shows
`Needs update`.

## Related

[Segmentation](segmentation.md) · [Region Size](region-size.md) · [Gating](gating.md) · [Figure](figure.md)
