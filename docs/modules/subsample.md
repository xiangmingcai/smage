# Subsample

Keeps a fixed random number of events per file for everything downstream —
useful when a file has millions of events and only a sample is needed for
clustering, embedding or plotting.

![Subsample](../assets/screenshots/subsample.png)

## Settings

| Setting | Description |
|---|---|
| Files | The files to sample. |
| Count by | Whether the target is counted per file or in total. |
| Points per file | Target number of events. |
| Allocation | How the total is distributed across files (equal, proportional to file size, …). |

## Output

A row subset. Downstream modules see only the sampled rows; unsampled rows are
reported as *not evaluated* (NaN) where a full-length column is required.

## Notes

- The sample is deterministic for a given file and target size, so re-running
  an analysis reproduces the same rows.
- Plotting and gate statistics operate on the sampled rows, which keeps
  interactive work fast.

## Related

[Subset](subset.md) · [Cluster](cluster.md) · [Dimensionality Reduction](dimred.md)
