# Region

Detects and manages **image regions** (for example tumour, damage or empty
areas) and exposes each Region to downstream modules as a **spatial gate**.
It works only with image files.

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
| Left bottom | The **Region tree**: Region → Sample → Sub-region. |
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
`in/out` rings and anchors, so dragging an anchor can also create a new hole.

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
3. Threshold: **Auto** uses Otsu inside the detection gate; untick Auto to
   enter a fixed value.
4. Morphological expansion joins nearby signal.
5. Fill holes. **Max hole area** limits this to holes up to that many
   pixels; 0 fills every enclosed hole. Larger lumens stay open.
6. Clip to the detection gate.
7. Remove components below **Min area**.
8. Trace each component's outline and simplify it to a polygon
   (**Tolerance**, in pixels). Enclosed holes stay empty (stored as `out` rings), so a ring of
   cells does not become a filled disc over a lumen.

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
reports its own elapsed time, so slow stages are easy to spot. The lines use
the file names shown in the project, not internal parquet ids. The log is
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
- Region and Sub-region can be renamed; Sample cannot.
- **Add New Region** creates an empty Region.
- **Add Sample…** adds files that are not yet in that Region.
- **Add Sub-region** is available only while the image shows that Sample;
  it uses the Region in tool, so draw one polygon and it becomes a new
  Sub-region.
- Region/Sub-region can be copied and pasted as a whole branch. A pasted
  sub-region can target a Sample in another file; its in/out rings are copied
  as-is.
- Selecting a Sub-region shows every `in/out` ring as an editable polygon.
  Dragging an anchor re-traces the Sub-region through a pixel mask, so a new
  enclosed hole becomes an `out` ring automatically.
- Clicking a filled Sub-region in the image selects that Sub-region and its
  Sample in the Region tree.

## Drawing style

Each Region has its own fill colour, fill opacity (0% = no fill), edge colour
and edge width. All sub-regions of a Region share that style; the selected
Region is outlined more strongly. Styles affect the image only.

## What downstream modules see

Only **Regions** are exposed, as spatial gates. Each Sub-region is the
even-odd fill of its `in/out` rings, and its holes never subtract from
another Sub-region.
The internal gate tree is never exposed. Figure filters and image overlays,
Gate2Col 0/1 columns, Heatmap annotations and Region Size can all use them.
A file without a Sample for a Region is treated as not contained in that
Region; a Sample with no sub-regions is an empty region (area 0).

## Saving

Press **Save** to write the gates (including per-file shapes), the Region
tree and the detection settings to `Module/<id>/regions.json`. Downstream
modules read the saved state; unsaved edits stay in the editor.

## Related

[Segmentation](segmentation.md) · [Region Size](region-size.md) · [Gating](gating.md) · [Figure](figure.md)
