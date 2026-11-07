"""Small shared constants that do not belong to a specific layer."""
from __future__ import annotations

# Most module logs keep only the newest lines; the exporters use the same cap.
_FEATURE_LOG_LIMIT = 400

# Open-module registry states: a module is unmodified, modified-but-
# unsaved, or just saved (transient).
MODULE_STATE_UNMODIFIED = "unmodified"
MODULE_STATE_MODIFIED = "modified"
MODULE_STATE_SAVED = "saved"

APP_NAME = "Smage"
