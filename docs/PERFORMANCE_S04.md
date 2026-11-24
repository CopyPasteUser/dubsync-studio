# S04 — Native UI architecture (incremental)

This branch is independent of S01–S03. Extract the cooperative Qt task worker
from MainWindow, and the timeline widget and formatted-time helper from the
review workbench. Existing module-level imports remain compatible. No UI
layout, shortcuts, task lifecycle, project state or media/export rules are
intentionally changed. Static worker contract follows the relocated source.

TimelineIndex snapshots the existing visible-anchor predicate on set_data and
uses bisect for inclusive viewport selection. It does not authorize exports,
open files or claim continuous verification. Rebuild it when the alignment
changes; cursor movement and zoom only query the prepared index. Disabled
segments, mismatched anchors and unmapped regions remain excluded. Nonfinite
target timestamps are ignored. Remove the previously unused calculation of
safe_for_automatic_export from paintEvent. No safety gate is removed from the
actual engine or evidence panel.

## Partial local validation

Five pure index tests passed in a standalone workspace. Synthetic benchmark:
5,000 anchors, 500 changing windows, five alternating runs; all visible anchor
tuples exactly equal. Median old repeated filtering 583.187 ms, new index
construction plus queries 1.902 ms. This is a Python projection microbenchmark,
not a Qt paint, frame-rate or UI responsiveness claim. The actual draw loop
still renders visible anchors and evidence spans.

The committed helper and tests must match local blob IDs
354223c013212869e8553dce747696217cf00f9d and
49f4db20483ce39257a7e10690153955c74145e6 respectively.

## Remaining work

Run Qt workbench, cancellation, project state, all GUI and release tests.
Measure populated episode switching, startup and idle PSS via S01; lazy
secondary pages, track component reuse and broader MainWindow controller
extraction are pending. No visual macOS redesign yet. Full checkout and
PySide6 are unavailable in this execution environment; no merge or actual
Qt smoke claim. Keep S05 design changes separate.
