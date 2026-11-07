# Defects found by the regression suite

## BUG-1 — `MainWindow.closeEvent` is shadowed by `QMainWindow.closeEvent`

- **Severity:** Major
- **Detected by:** `shell.close_prompt` (quarantined so the suite stays usable)
- **Date:** 2026-11-07
- **Expected:** closing the window with unsaved module changes shows the
  Yes/No prompt; `No` keeps the window open; `Yes` saves the window geometry,
  discards draft folders and stops the session log.
- **Actual:** `window.close()` closes immediately without prompting.
  `ProjectIOMixin.closeEvent` (window geometry, draft cleanup, `runlog.stop`)
  never runs.
- **Cause:** `MainWindow(QMainWindow, ..., ProjectIOMixin, ...)` resolves
  `closeEvent` to the `QMainWindow` virtual before the mixin's method.
- **Suggested fix (not applied — policy is to report first):**
  ```python
  # src/smage/main.py, inside MainWindow
  def closeEvent(self, event):
      return ProjectIOMixin.closeEvent(self, event)
  ```
  or move the mixins before `QMainWindow` in the base list.
- **Status:** fixed on 2026-11-07 by adding a forwarding `closeEvent` in
  `MainWindow`. The `shell.close_prompt` case is no longer quarantined and now
  enforces the behaviour.
