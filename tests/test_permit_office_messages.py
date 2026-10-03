"""Message helpers: the optional log file that keeps GP evidence on disk."""

from __future__ import annotations

from types import SimpleNamespace
import sys

sys.modules.setdefault(
    "arcpy",
    SimpleNamespace(AddMessage=lambda *_: None, AddWarning=lambda *_: None, AddError=lambda *_: None),
)

from toolbox.permit_office_arcgis import messages


class _Sink:
    def __init__(self):
        self.lines = []

    def addMessage(self, line):
        self.lines.append(line)

    addWarningMessage = addMessage
    addErrorMessage = addMessage


def test_log_file_env_appends_every_tagged_line_with_utc_stamp(tmp_path, monkeypatch):
    """Verify PERMIT_OFFICE_LOG_FILE keeps a copy of each message (AR18 run 8 lost the GP log)."""

    path = tmp_path / "gp.log"
    monkeypatch.setenv(messages.LOG_FILE_ENV, str(path))
    sink = _Sink()

    messages._log(sink, "REDRAW", "feature-query target='PermitPoints'")
    messages._warn(sink, "REDRAW", "slot failed")
    messages._err(sink, "DASH", "boom")

    assert sink.lines == ["[REDRAW] feature-query target='PermitPoints'", "[REDRAW] WARN: slot failed", "[DASH] ERROR: boom"]
    written = path.read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in written] == sink.lines
    assert all(line[:4].isdigit() and line.split(" ", 1)[0].endswith("+00:00") for line in written)


def test_log_file_unset_or_unwritable_never_breaks_messages(tmp_path, monkeypatch):
    """Verify the file copy is optional and a bad path is ignored."""

    sink = _Sink()
    monkeypatch.delenv(messages.LOG_FILE_ENV, raising=False)
    messages._log(sink, "X", "plain")
    monkeypatch.setenv(messages.LOG_FILE_ENV, str(tmp_path / "missing-dir" / "gp.log"))
    messages._log(sink, "X", "still delivered")

    assert sink.lines == ["[X] plain", "[X] still delivered"]
