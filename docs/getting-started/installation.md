# Installation

## Requirements

- **Windows 10 or Windows 11, 64-bit.**
- No Python installation is required for the packaged build.
- A local disk for the application folder and your projects (network drives
  work for the project folder, but are slower).

## Install the portable build

1. Open the [latest release](https://github.com/xiangmingcai/smage/releases/latest).
2. Download `Smage-vX.Y.Z-win64.zip` and its `SHA256SUMS.txt`.
3. Verify the download (optional but recommended):

    ```bat
    certutil -hashfile Smage-v0.5.5-win64.zip SHA256
    ```

    The value must match the line in `SHA256SUMS.txt`.
4. Extract the **whole** `Smage` folder to a local disk, for example
   `D:\Smage`. Keep `Smage.exe` and the `_internal` folder together — running
   the executable from inside the `.zip` will not work.
5. Double-click `Smage.exe`.

## The SmartScreen warning

The current builds are **not code-signed yet**, so Windows may show
"Windows protected your PC" the first time you start Smage.

1. Click **More info**.
2. Click **Run anyway**.

If the **Run anyway** button is missing, or the app still does not start, see
[Windows protected your PC / the app won't run](../reference/windows-protected-your-pc.md)
for the file-unblock, Smart App Control and managed-device cases.

## First start

Smage opens an empty window. Create a project with
`File → New Project…` and continue with the [quick start](quickstart.md).

## Upgrading

Replace the old `Smage` folder with the new one. Projects are independent of
the application folder, so they keep working. Global settings are kept in
`%APPDATA%\Smage\` and are not overwritten.

## Uninstalling

Delete the `Smage` folder. If you also want to remove global settings,
delete `%APPDATA%\Smage\`. Project folders (`.smage`) are yours to keep or
delete.

## Optional checks

Run these from a command prompt in the Smage folder:

```bat
Smage.exe --self-test
```

Verifies that every bundled module can be imported; the report is written to
`%APPDATA%\Smage\selfcheck.log`.

```bat
Smage.exe --run-tests --tag e2e
```

Runs the bundled end-to-end regression suite in the packaged environment.
Results are written to `%TEMP%\SmageTests\frozen-latest.md`.
