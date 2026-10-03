"""Message adapters that work with ArcGIS tool messages and global ArcPy logs."""

from __future__ import annotations

from datetime import datetime, timezone
import os

import arcpy


# Probe-only: when set, every tagged message is also appended to this file with
# a UTC timestamp, so live evidence survives Pro offloading GP messages (AR18).
LOG_FILE_ENV = "PERMIT_OFFICE_LOG_FILE"


def _copy_to_log_file(line):
    path = os.environ.get(LOG_FILE_ENV, "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
    except OSError:
        pass


def _log(messages, tag, text):
    """Write an informational message through ArcGIS or ArcPy fallback logs."""

    line = f"[{tag}] {text}"
    _copy_to_log_file(line)
    try:
        messages.addMessage(line)
    except Exception:
        arcpy.AddMessage(line)


def _warn(messages, tag, text):
    """Write a warning message through ArcGIS or ArcPy fallback logs."""

    line = f"[{tag}] WARN: {text}"
    _copy_to_log_file(line)
    try:
        messages.addWarningMessage(line)
    except Exception:
        arcpy.AddWarning(line)


def _err(messages, tag, text):
    """Write an error message through ArcGIS or ArcPy fallback logs."""

    line = f"[{tag}] ERROR: {text}"
    _copy_to_log_file(line)
    try:
        messages.addErrorMessage(line)
    except Exception:
        arcpy.AddError(line)
