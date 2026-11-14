# Region

Detects and manages **image regions** (for example tumour, damage or empty
areas) and exposes each Region to downstream modules as a **spatial gate**.
It works only with image files.

A Region can consist of several **sub-region** polygons on one file, and the
same Region may have different polygons on different files. Presence is
per-file: a file may not contain a Region at all, or may contain it with no
sub-regions (an empty Region).

A new Region module starts with **no image files selected**. Use
**Select files…** to choose them; the preview and detection stay empty until
you do.

## The page

| Area | Contents |
|---|---|
| Left top | Image files used by this module. |
| Left middle | Internal **detection gates** (rect / ellipse / polygon / boolean). |
| Left bottom | The **Region tree**: Region → Sample → Sub-region. |
| Middle | Image with the gate tools; the detection panel is below it. |
| Right top | Image channel, level and display settings. |
| Right bottom | Drawing style of the selected Region. |

## Internal detection gates

The gate tree is local to this module and is never visible downstream. A gate
defines **where** detection is allowed to run: the selected gate and its
parents are combined, and detection results are clipped to that area. Select
**Root** to detect over the whole image.

Draw a gate with the Rectangle, Ellipse or Polygon tool while a gate (or
Root) is selected in the tree; the new gate becomes its child. Double-click a
gate to rename it; the context menu deletes it. Gates with children must have
their children removed first, and a gate used by a boolean gate cannot be
deleted until that boolean gate is deleted.

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

1. Read the selected channel through the pipeline.
2. Gaussian smoothing.
3. Threshold: **Auto** uses Otsu inside the detection gate; untick Auto to
   enter a fixed value.
4. Morphological expansion joins nearby signal.
5. Fill holes.
6. Clip to the detection gate.
7. Remove components below **Min area**.
8. Trace each component's outline and simplify it to a polygon
   (**Tolerance**, in pixels).

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

Detection runs in the background and reports progress per file. Every run
creates a **new Region** named `Region 1`, `Region 2`, …; it never replaces or
merges an existing Region. Files with no detected polygons are kept as empty
Samples.

## Region tree

- **Show** controls only the image overlay, never what downstream modules see.
- Region and Sub-region can be renamed; Sample cannot.
- **Add New Region** creates an empty Region.
- **Add Sample…** adds files that are not yet in that Region.
- **Add Sub-region** is available only while the image shows that Sample;
  draw one polygon, then use Add Sub-region again for another one.
- Region/Sub-region can be copied and pasted as a whole branch. A pasted
  sub-region can target a Sample in another file; polygon coordinates are
  copied as-is.
- Sub-region anchors can be dragged on the image after selecting the polygon.

## Drawing style

Each Region has its own fill colour, fill opacity (0% = no fill), edge colour
and edge width. All sub-regions of a Region share that style; the selected
Region is outlined more strongly. Styles affect the image only.

## What downstream modules see

Only **Regions** are exposed, as spatial gates (the union of their polygons).
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
