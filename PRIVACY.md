# Privacy and Network Access

_Last updated: 2026-11-16_

Smage is a local desktop application for research use. This page explains
what data it stores, what it does not collect, and when it may use the
network.

## Summary

- **No telemetry, analytics or crash uploads.**
- **No account is required.**
- **Your images, tables and analysis results stay on your computer.**
- Network access only happens for actions you start, such as installing an
  optional extension or opening a documentation link.

## Data stored on your computer

Smage writes only to locations you choose or to the current Windows user
profile:

- the project folder (`<name>.smage`), containing imported data, settings,
  generated masks/channels and project logs;
- `%APPDATA%\Smage\` for global settings (`settings.json`), extension
  settings (`extensions.json`), the self-check report and diagnostic logs;
- temporary files created by calculations or by the optional external
  Python environments.

These files are not uploaded automatically. You can delete them at any time;
remove a project by deleting its `.smage` folder.

## Network access

The Smage application itself does not send project data anywhere and does
not check for updates automatically. Network access can occur in these
user-initiated cases:

- **Extension Manager:** installing or repairing an optional extension runs
  `pip` in a separate Python environment. That process downloads packages
  from PyPI (or an index you configure).
- **Model downloads:** some extensions download model weights on first use
  or from a **Download model** button. Those downloads come from the model
  provider configured by that extension.
- **Documentation and links:** opening a help or documentation link uses
  your default browser.
- **External tools:** if you configure a separate Python environment or
  other external software, its own network behavior is outside Smage's
  control.

Smage does not operate a server, does not proxy your data, and does not share
data with third parties.

## Optional extensions

Extensions are installed into separate Python environments and are governed
by their own licenses and privacy terms. Smage does not bundle their model
weights. Review the extension's own documentation before installing it.

## Research data and clinical use

You are responsible for the ethical and legal handling of your data,
including consent, ethics or IRB approval, and institutional requirements.
Smage is **not a medical device** and must not be used for clinical decisions.
See [DISCLAIMER.md](DISCLAIMER.md).

## Reporting a problem

Bugs and privacy questions can be raised at
<https://github.com/xiangmingcai/smage/issues>. Security problems should
follow [SECURITY.md](SECURITY.md) and must not be posted publicly.
