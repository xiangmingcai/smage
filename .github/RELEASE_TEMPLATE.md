# Smage vX.Y.Z

## Highlights

- 

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

> **Code signing:** Smage uses the SignPath Foundation for code signing of
> its Windows releases. The application is pending, so this build is not
> signed yet. See
> [CODE_SIGNING_POLICY.md](https://github.com/xiangmingcai/smage/blob/main/CODE_SIGNING_POLICY.md).
> Once signing is active, this section will state: "Free code signing
> provided by SignPath.io, certificate by SignPath Foundation."

## Notes

- Smage is research software, not a medical device, and must not be used for
  clinical decisions. See
  [DISCLAIMER.md](https://github.com/xiangmingcai/smage/blob/main/DISCLAIMER.md).
- Smage is released under GPL-3.0 with the approval of Amsterdam UMC. See
  [LICENSE](https://github.com/xiangmingcai/smage/blob/main/LICENSE) and
  [PRIVACY.md](https://github.com/xiangmingcai/smage/blob/main/PRIVACY.md).
- The full changelog is in
  [CHANGELOG.md](https://github.com/xiangmingcai/smage/blob/main/CHANGELOG.md).
