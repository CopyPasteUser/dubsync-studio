# DubSync Studio UX contract

## Business evidence
README.md describes the scan/analysis/export flow and the profile budgets.
docs/ARCHITECTURE.md owns release invariants. Source files are never modified;
existing exports require authorization; uncertain alignment is never labelled safe.
core/project.py and core/session.py own explicit project persistence and fresh startup.
No billing, authentication or cloud CRUD is present.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
| --- | --- | --- | --- | --- |
| Table Selection | MainWindow.episode_table / QTableWidget | Qt single row and group key mapping | local in-memory filtering | tests/test_studio_workspace.py |
| Select/Listbox | QComboBox | Qt keyboard and popup behavior | native | tests/test_gui_smoke.py |
| Form | SourceRow / PerformanceSettingsDialog | core/models.py and core/performance.py | native Qt fields | tests/test_gui_smoke.py |
| Scrollbar | theme.py / QScrollArea | DESIGN.md | vertical/horizontal, high contrast | tests/test_studio_workspace.py |
| Toast | MainWindow insight panel and QStatusBar | main_window.py | persistent error, transient status | tests/test_studio_workspace.py |
| CRUD | MainWindow project handlers | core/project.py / docs/ARCHITECTURE.md | native QFileDialog/QMessageBox | tests/test_project.py |
| Section Editor | SegmentEditorDialog / QTableWidget | core/models.py `validate_timeline_layout` | native time spinboxes, split, toggle, reset | tests/test_segment_editor.py; real-device QA pending |
| Frame fingerprint cache | PerformanceSettingsDialog / QCheckBox | core/performance.py, core/frame_cache.py | on/off, default on; result cache always off | tests/test_frame_cache.py, tests/test_gui_smoke.py |
| Decoder workers | PerformanceSettingsDialog / QComboBox | core/performance.py, core/alignment.py | 1/2, effective count resource- and source-dependent | tests/test_parallel_sampling.py, tests/test_performance.py |
| Adaptive resource presets | MainWindow.profile_quick_combo / PerformanceSettingsDialog | core/performance.py | Auto recommends one of Leicht/Mittel/Stark; custom retains manual budgets | tests/test_performance.py; tests/test_gui_smoke.py; tests/test_studio_workspace.py |
| CPU decoder autotuning | gui/autotune.py / PerformanceSettingsDialog | core/autotune.py + core/performance.py host-bound opt-in | local single-file QThread benchmark, exact parity gate, explicit accept then settings save; NAS excluded | tests/test_autotune.py; tests/test_gui_smoke.py; tests/test_parallel_sampling.py |
| Durable batch queue | BatchQueueDialog / QTableWidget | core/job_queue.py SQLite entries; core/batch_diagnostics.py proof | explicit start/restart/retry/drain; no implicit startup | tests/test_job_queue.py; tests/test_gui_smoke.py |
| Inline queue room | QueueControlRoom / QTableWidget | same BatchJobQueue SQLite `recent()`; original start/retry/drain callbacks | compact empty state with Studio navigation; bounded populated list; load failures remain visible; original dialog retained | tests/test_workbench_2.py; tests/test_studio_workspace.py |
| Episode onboarding | MainWindow.library_empty_action / Studio viewport | existing source list and episode filter; scan remains explicit | no sources → choose folder; sources with no results yet → scan; filter miss → reset. 980×680 Studio scrolls above fixed job dock; 1440×900 avoids outer scroll | tests/test_studio_workspace.py |
| Focused comparison | MainWindow.review_focus_button / QSplitter | current Studio widgets and saved Qt splitter state | `Alt+0` expands evidence pane; navigation preserves focus state; toggle restores side panes and their widths, never replaces editor state | tests/test_studio_workspace.py |
| Visual review | StudioReviewPanel / AlignmentTimeline | live selected AlignmentResult and MediaInfo | anchor/segment plot; user-chosen source; explicit two-still loading | tests/test_workbench_2.py; real NAS pending |
| Visual evidence | AlignmentTimeline / core/evidence.py | observed anchors plus immutable alignment mapping | sampled / unverified / unmapped; largest-gap navigation; no export mutation | tests/test_evidence.py |
| Audio A/B | StudioReviewPanel / gui/audio_review.py | explicit 18 s FFmpeg excerpts | two independent waveforms; unmatched source remains empty | tests/test_audio_review.py |
| Human-approved edition assignment | EpisodeExplorerDialog / MainWindow, core/episode_assignment.py | freshly evaluated independent interior visual evidence, project-local manual_candidate_assignments | explicit verified-row selection + checkbox; never batch; single export checks new independent alignment | tests/test_episode_assignment.py; tests/test_episode_explorer_gui.py |
| Longer A/B playback | SyncPreviewDialog / AudioPreviewTransport / core/audio_audition.py | bounded private FFmpeg WAV excerpts, verified segment-local slope | user-selected 12/30/60 s; optional prefetch and auto-next only within confirmed source/reference bounds; manual Stop cancels continuation; four LRU previews; no gapless claim | tests/test_audio_ab_transport.py |
| Single export preflight | ExportPreflightDialog / core/export_preflight.py | current selected master, tracks, destination, visual evidence | native table with ready/caution/blocked; blocked disables action; exporter remains final authority | tests/test_export_preflight_gui.py |
| GPU self-test | gui/decoder_test.py / core/ffmpeg_sampling.py | measured CPU/GPU frame parity and throughput | explicit local file, read-only worker, cancel | tests/test_decoder_test.py |

## Flow ledger
- Add sources: picker or drag/drop → roots list → explicit scan; no implicit export.
- Scan: existing worker → deterministic groups → selected group metadata. Collapse
  source list after scan, retain all roots, expose toggle to reopen it.
- Search: local debounce; clearing cancels timer and restores matching rows immediately.
  Session-only query. Native scroll viewport, full local dataset; no remote pagination.
- Navigate: retain selection, corrections, filter, profile and destination.
- Visual review: master/source selection resets any old frame; timeline cursor
  changes invalidate in-flight stills. A source time without a valid segment
  is displayed as *unpaired*, never as an invented correspondence. Frame reads
  are manual, bounded in time/size and read-only. Manual slope/offset values are
  presented as provisional preview mappings and never turn a match safe.
- Evidence gaps and waveforms: sample neighborhoods are never a claim of
  continuous frame-by-frame inspection. Jumping to a gap does not modify a
  result. Waveform reads require a click and are bounded in duration, RAM and
  time; stale results are discarded when source/cursor changes.
- A/B listening: the reference-position A/B switch is always manual. The
  optional continuation checkbox preloads one following 60-second-or-shorter
  WAV while the current excerpt plays; prefetch must not interrupt playback.
  End-of-media waits for a slow prefetch, or stops where source, reference or
  confirmed cut coverage ends. Manual Stop prevents auto-resume. A different
  track or reference point resets the sequence. Media extraction and four-file
  private LRU cache remain bounded, cancellable, and unrelated to export proof.
  An external player handles standalone excerpts only, not automatic playback.
- Inline batch control: reading the queue never executes it. Start and retry
  use the same verified core actions/authorization as the legacy queue dialog;
  automatic drain processes queued entries only. Real progress is refreshed
  while a queued worker is active without polling media files.
- Batch empty state: an absent saved queue shows one short explanation and a
  native **Zum Studio** navigation action, not a full-size empty table with
  disabled job actions. Queue read failures remain visible. Detected
  multilingual episode groups are labelled separately from persisted jobs;
  neither label establishes permission to export.
- Studio empty states: adding a source never triggers analysis by itself;
  scanning requires an explicit press. A filter miss cannot delete results;
  its reset button only clears search/filter. The episode work area can scroll
  on short viewports without moving the persistent processing dock.
- Focused Studio comparison is a reversible presentation-only toggle: hiding
  the episode list and track inspector preserves their widgets, edits, selection
  and unsaved-change state. Their previous splitter geometry returns when the
  user exits focus mode. Full episode titles and audio track names remain
  available via tooltips; source paths can be selected for copying.
- Analyse/export: existing QThread → progress dock → existing completion/error reports.
  Duplicate tasks and profile changes are disabled during processing. Pause remains
  cooperative and cancel releases pause. No new domain transitions introduced.
- Unsafe/cancelled/error: explicit insight and full reports, not green success.
- Candidate adoption: a ranked visual match has no side effects by itself.
  An explicit checked approval maps the exact verified file to a project group,
  moving any old scanner ownership while retaining the empty former group.
  No source is renamed, automatic season processing excludes adopted groups,
  and a fresh independent interior video proof is mandatory at single export;
  manual timing corrections cannot bypass this requirement.
- Export preflight: the selected-file export shows an accessible reasons table
  after the output filename and overwrite decision. Blocked rows disable Start;
  the exporter still performs full media verification and atomic finalization.
- Video-fingerprint cache: explicit on/off in performance settings. Hits show
  file-validated sample reuse; alignment decisions and export safety are always
  recomputed. Cache failures fall back to decoding.
- Video workers: selecting two requests concurrent source/master sampling on
  capable local hardware. NAS paths and GPU-autobenchmark use one worker;
  effective count and reason appear in the alignment report. Decoder changes
  never change export safety rules.
- GPU auto preference: remembered only in the running process for a codec and
  current device set. Each new file still gets fresh CPU/GPU validation; the
  full device shortlist is refreshed periodically or after backend failure.
- Resource presets affect CPU/worker concurrency only; the same sample interval,
  frame budget and added-audio bitrate are kept across preset changes. The
  persisted preset is resolved against the currently available hardware each
  launch; custom numeric budgets remain explicitly bounded. Auto-decoder
  preview lists only candidates, never asserts proven GPU execution.
- CPU autotuning is initiated only from performance settings, processes a
  representative local video in a cancellable worker and leaves existing
  settings unchanged unless a verified ≥15% recommendation passes exact
  fingerprint and per-window regression checks, the user accepts it, and
  subsequently saves the parent form. A change of CPU model/CPU quota,
  manual thread count, or resource preset revokes the calibration. Dual
  decoder runs retain the per-worker CPU cap; GPU and export safety are
  untouched. A single media file is not a universal performance proof.
- Batch continuation: optional explicit journal file selection on the series
  page; incompatible reports fail visibly. Only per-episode matched recipe,
  sources, output signature and fresh video/audio probe count as verified.
  Existing-file skip/overwrite authorization is never changed by a journal.
- Batch queue: save the exact selected groups and settings before execution;
  allow "only queue" and "start now". After app restart, display persisted jobs
  but do not auto-run them. A resumed queue job can skip only journal-verified
  output and cannot overwrite existing output. Reconfirm any *fresh* queued
  job's overwrite permission at execution; retry creates a new safe subset.
- Section editing: explicit per-track dialog → structural validation → manual safety flag →
  required export confirmation. Disabled intervals are silent, and batch skips
  episodes containing manually edited sources. Selection/offset inspection and
  undo/redo are local to the modal editor; rejecting modified segments asks for
  confirmation. Project saving fingerprints both files.
- Project replace/exit: existing unsaved-change dialog remains authoritative.
- Remove source: changes project membership only; never deletes media.

## Native platform contract
Qt handles focus traversal, dialogs, selection and accessible roles. No web ARIA,
URL query persistence or browser dialogs are applicable. Native combobox popup
geometry is accepted. Table and inspector scroll independently; export form has
one scroll owner. No animation dependency. All added labels use German.

## Verification limits
The section editor has targeted Qt offscreen tests; desktop compositor,
real file-picker integration and Linux screen-reader behavior need device QA;
static web audits cannot validate this Python/Qt implementation.
