# Region Size

Calculates the **pixel area of every Region** in every selected image file.
It replaces the old Area module and produces a report table only; it does not
change the data that flows downstream.

## Inputs

| Side | Contents |
|---|---|
| Left | Image files. |
| Middle | Regions from every upstream [Region](region.md) module; **Calculate** at the bottom. |
| Right | The file × Region area table. |

Regions from several upstream Region modules can be combined. If two
selected Regions share a name, the column label is prefixed with the module
name (`Module/Region`).

A new Region Size module starts with **no image files selected**. Use
**Select files…** to choose them before pressing **Calculate**.

## Results

- One row per selected file, one column per selected Region.
- The value is the number of pixels inside the union of the Region's
  sub-regions. Overlapping sub-regions are counted once.
- A file **without** a Sample for a Region shows **NA** (not 0).
- A Sample with **no sub-regions** shows **0**.
- Upstream row filtering (for example a Subsample module) is respected: only
  rows visible at this module are counted.

The table is sorted by file name and Region name by default. Drag the row
handle or a Region column header to change the order.

**Copy TSV** copies the current visual order with a `File name` first header,
ready to paste into Excel.

## Calculate and locking

Press **Calculate** to run the area calculation in the background. The
results are saved with the module, and the upstream modules are **locked**
until:

- **Clear results** is pressed, which removes the table and unlocks upstream
  modules; or
- the module is deleted.

Editing the file or Region selection does not change the table until
Calculate is pressed again; the column labels (including source prefixes)
also update only on Calculate.
