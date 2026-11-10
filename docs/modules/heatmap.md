# Heatmap

Shows a matrix of feature values (for example the mean marker intensity per
cluster) with optional row/column clustering and a dendrogram.

![Heatmap](../assets/screenshots/heatmap.png)

## Settings

| Setting | Description |
|---|---|
| Sampling | For large files: `Uniform per file` or `Proportional`, with a rows-per-file limit. Sampling happens before gating. |
| Cell width / height | Size of each matrix cell. |
| Colormap | Color scale for the values. |
| Normalize | Scale values per row, per column or globally. |
| Number color / decimals | Show the numeric value in each cell. |
| Min / Max | Color range. |
| Distance / linkage | Metric and linkage used for clustering. |
| Dendro width / height | Size of the row/column dendrograms. |

## Output

A live heatmap view; export it through [Export](export.md) or the module's own
export action.

## Notes

- The heatmap reads the feature table produced by
  [Feature Down](feature-down.md) (or any table with a class column).
- Clustering is computed on the aggregated matrix, not on the event table, so
  it stays fast even for large experiments.

## Related

[Feature Down](feature-down.md) · [Figure](figure.md) · [Export](export.md)
