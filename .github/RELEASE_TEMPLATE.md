# Smage vX.Y.Z

## Highlights

- **Export — Image Export** now shows each file's **Highest dtype** and an
  **Est. size**, plus a **Know more about data type** reference. A **Data
  type** setting writes each file's own highest dtype or one dtype for the
  whole batch; the log lists every channel as `source dtype → export dtype`.
- **Segmentation** has a checkbox in the **Expose** column header that checks
  or clears every file at once.
- Module logs keep up to 10,000 lines, are stored as they are written and are
  restored when a module is reopened.
- Unmix outputs created before 0.4.3 are visible to downstream modules again.

## Downloads

- `Smage-vX.Y.Z-win64.zip` — portable Windows bundle (extract and run)
- `SHA256SUMS.txt` — checksum for the archive

## Installation

1. Download `Smage-vX.Y.Z-win64.zip` and `SHA256SUMS.txt`.
2. Extract the whole `Smage` folder to a local disk.
3. Double-click `Smage.exe`.

Windows 10 or 11, 64-bit. No Python installation is required.

## Verification

```bat
certutil -hashfile Smage-vX.Y.Z-win64.zip SHA256
```

Compare the result with `SHA256SUMS.txt`.

> **Code signing:** this build is not signed yet. Code signing will be added
> once the project has established enough adoption for an approved signing
> provider; the planned process is in
> [CODE_SIGNING_POLICY.md](https://github.com/xiangmingcai/smage/blob/main/CODE_SIGNING_POLICY.md).
> Verify the download with `SHA256SUMS.txt`.

## Notes

- Smage is research software, not a medical device, and must not be used for
  clinical decisions. See
  [DISCLAIMER.md](https://github.com/xiangmingcai/smage/blob/main/DISCLAIMER.md).
- Smage is released under GPL-3.0 with the approval of Amsterdam UMC. See
  [LICENSE](https://github.com/xiangmingcai/smage/blob/main/LICENSE) and
  [PRIVACY.md](https://github.com/xiangmingcai/smage/blob/main/PRIVACY.md).
- The full changelog is in
  [CHANGELOG.md](https://github.com/xiangmingcai/smage/blob/main/CHANGELOG.md).
