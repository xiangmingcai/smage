# Windows protected your PC / the app won't run

Windows may block `Smage.exe` because the current builds are **not
code-signed**. Depending on the Windows version and device policy, the dialog
may or may not show a **Run anyway** button. This page explains which
mechanism is blocking the app and how to handle it safely.

Smage is distributed only through the official
[GitHub Releases](https://github.com/xiangmingcai/smage/releases) page. Do not
disable Windows security features for a copy from another source.

## 1. If "More info → Run anyway" is available

Click **More info**, then **Run anyway**. This is the normal prompt for an
unsigned application.

## 2. If the button is missing: unblock the downloaded file

Windows marks files downloaded from the internet with a **Mark of the Web**.
Removing it often restores the Run anyway button or allows the app to start.

For the downloaded **zip**, before extracting:

1. Right-click the zip → **Properties**.
2. Open the **General** tab.
3. At the bottom, in the **Security** section, tick **Unblock**.
4. Click **Apply → OK**, then extract the zip.

After extracting, check `Smage.exe` the same way. If the checkbox is missing,
the file has no Mark of the Web and this is not the cause.

PowerShell equivalent:

```powershell
Unblock-File -Path "C:\path\to\Smage.exe"
```

For the whole extracted folder:

```powershell
Get-ChildItem -Path "C:\path\to\Smage" -Recurse | Unblock-File
```

## 3. If it still blocks: Smart App Control

Windows 11 can enable **Smart App Control**, which blocks unsigned apps
**without a Run anyway button and without a per-app allow list**. Unblocking
the file has no effect on it.

Check the state:

1. Open **Windows Security**.
2. Go to **App & browser control**.
3. Open **Smart App Control settings** (shortcut:
   `windowsdefender://smartapp/`).
4. The state is **On**, **Evaluation** or **Off**.

If it is **On** and you are on a personal PC, set it to **Off**. Notes:

- There is no per-application exception.
- On some Windows builds turning it off is permanent until Windows is reset
  or reinstalled; newer builds can turn it back on.
- On a managed work or university PC the setting may be unavailable or
  controlled by IT policy.

Do not turn off Microsoft Defender antivirus itself; Smart App Control and
Defender are separate features.

## 4. Managed / university computers

If the PC is managed by IT (for example an Amsterdam UMC laptop), group
policy may use **Warn and prevent bypass**, which deliberately hides the Run
anyway button. In that case only IT can allow the application, for example by
approving it, signing it, or changing the policy. Contact your IT service
desk rather than changing system security settings.

## 5. Confirm what blocked the app

- **Windows Security → Virus & threat protection → Protection history**:
  look for **Smart App Control** or **Blocked app** entries.
- **Event Viewer → Applications and Services Logs → Microsoft → Windows →
  CodeIntegrity → Operational**: event **3077** shows the blocked file.

These entries distinguish SmartScreen/Mark of the Web from Smart App Control
or an organizational policy.

## 6. Verify the download

Every release includes `SHA256SUMS.txt`. Before running the application, you
can verify the zip:

```bat
certutil -hashfile Smage-vX.Y.Z-win64.zip SHA256
```

Compare the result with the matching line in `SHA256SUMS.txt`.

## Related

- [Installation](../getting-started/installation.md)
- [FAQ](faq.md)
- [Code signing policy](https://github.com/xiangmingcai/smage/blob/main/CODE_SIGNING_POLICY.md)
- [Releases](https://github.com/xiangmingcai/smage/releases)
