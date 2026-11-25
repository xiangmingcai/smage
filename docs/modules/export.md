# Export

Writes selected tables and images to disk. The module has two tabs: **Table
Export** and **Image Export**.

![Export](../assets/screenshots/export.png)

## Table Export

| Setting | Description |
|---|---|
| Data files | The tables to export (imported or generated files). |
| Columns | The columns to write, in the order shown (drag to reorder). |
| Output folder | Where the files are written. |
| Format | CSV, Parquet or FCS. |

## Image Export

| Setting | Description |
|---|---|
| Image files | The image tables to export. The table shows each file's **Highest dtype** and an **Est. size**. |
| Channels | The channels to write, in the order shown. |
| Output folder | Where the files are written. |
| Format | TIFF (uncompressed). |
| Data type | **Highest dtype of each file** (default) or **Highest dtype of all files**. |

A TIFF stores one data type for the whole file, so every channel of a file is
written with the same dtype. The dtype of a channel comes from where its
values live:

- imported columns use the dtype of the stored parquet column;
- generated columns (Unmix, Gate2Col, Region, Segmentation mask, Feature Up,
  Dimred, Cluster, Data Clean, Feature Down) use the dtype stored in their
  output file;
- values computed on the fly (Compensation, Illumination, Batch Align,
  Calculation) count as float32;
- Rename changes the name only and keeps the source dtype.

**Highest dtype of each file** uses the highest dtype among that file's
selected channels. **Highest dtype of all files** uses the highest dtype
found across every selected file, so the whole batch shares one type. Both
modes transfer the values unchanged: nothing is rescaled or clipped. The
**Est. size** column and the total use `width × height × channels × bytes per
sample`, the size of an uncompressed TIFF.

**Know more about data type** opens a reference with the range and precision
of every dtype, which software can open them, and how the two modes work.

The log lists every channel as `source dtype → export dtype` and repeats the
dtype of each written file.

## Output

One file per selected table or image in the output folder. The module keeps a
log of what was written and where.

## Notes

- Exported values are the pipeline's values. [Scale](scale.md) is
  display-only and is never applied to exported numbers.
- Gate columns can be added to the exported table with
  [Gate2Col](gate2col.md); per-class values can be added with
  [Feature Up](feature-up.md).
- uint64, int64, float16 and float64 are TIFF extensions; some viewers cannot
  open them. uint8, uint16 and float32 are the most portable choice.

## Related

[Figure](figure.md) · [Heatmap](heatmap.md) · [Gate2Col](gate2col.md)
