# Packaging

The Windows build is a PyInstaller **onedir** bundle; the release artifact is
the zipped `dist\Smage` folder.

## Build

```bat
.venv\Scripts\python.exe packaging\build_win.py
```

The script:

1. runs PyInstaller with `packaging/smage.spec`;
2. runs `Smage.exe --self-test` (imports every bundled module, including the
   dynamic imports);
3. runs the bundled end-to-end regression suite inside the frozen application
   (`Smage.exe --run-tests --tag e2e`).

Any failure fails the build. Results are written to
`%APPDATA%\Smage\selfcheck.log` and `%TEMP%\SmageTests\frozen-latest.md`.

## What the bundle contains

- `Smage.exe` plus the Python runtime and every dependency under `_internal/`.
- Application icons and external runner scripts.
- The regression suite, so users and maintainers can verify the build.

## Release checklist

1. Update `__version__` in `src/smage/__init__.py`.
2. Run `run_all.py --full`.
3. Run `packaging/build_win.py`; both self-checks must pass.
4. Zip `dist\Smage` as `Smage-vX.Y.Z-win64.zip`, generate `SHA256SUMS.txt`.
5. Create a GitHub release with the zip, the checksums and the release
   notes, starting from `.github/RELEASE_TEMPLATE.md`.
6. Code signing is currently not enabled. When an approved signing provider
   is in place, `Smage.exe` is signed in CI before the zip is written and
   signing uses a trusted timestamp. The planned scope and approval rules
   are in [CODE_SIGNING_POLICY.md](https://github.com/xiangmingcai/smage/blob/main/CODE_SIGNING_POLICY.md);
   the privacy/network statement is in
   [PRIVACY.md](https://github.com/xiangmingcai/smage/blob/main/PRIVACY.md).

## Automated release

Pushing a tag `v*` runs `.github/workflows/build-release.yml` on
`windows-latest`: the full regression suite, `packaging/build_win.py`
(self-test + frozen e2e), the portable zip, `SHA256SUMS.txt` and the GitHub
release. A manual **workflow_dispatch** run builds the same artifacts but
leaves them as Actions artifacts only.

The workflow installs `requirements-lock.txt`, so the same tag produces the
same dependency set. Update the lock whenever `requirements.txt` changes.

## Notes

- UPX is disabled in the spec; compressed executables trigger more antivirus
  false positives.
- The spec pins per-distribution metadata with `copy_metadata`, so
  `importlib.metadata` works inside the bundle.
- Per-module caches (Unmix matrices, illumination masks) are rebuilt on first
  use; nothing machine-specific is stored in the bundle.
