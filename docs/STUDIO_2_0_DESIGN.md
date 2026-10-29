# Studio 2.0 · Hallmark design decisions

Design status: implemented as native Qt/PySide6 workbench in `2.0.0b2`.
The target is a local, expert-dense desktop media tool, **not** a web landing
page. Hallmark's website hero, web-font and CSS-linter gates therefore do not
apply literally; the same visual review standard applies to the native UI.

## Direction and choices

| Decision | 2.0 choice | Reason |
| --- | --- | --- |
| Product register | Editing bench / control room | Episodes, proof and corrections are the actual work |
| Visual signature | Two-level image-anchor timeline with paired stills | A real technical object carries the visual hierarchy |
| Brand | Graphite, silver, sea-glass | Preserve the existing locked `DESIGN.md` contract |
| Type | Noto Sans / Segoe UI, native Qt fallbacks | Reuses installed desktop fonts; no web dependency |
| Structure | Three resizable panes with independent vertical scroll | Library, evidence and tracks remain in one view |
| Small window | Hide redundant Studio title below 950px; tab the monitor | Maintain 980×680 access without tiny controls |
| Export | Persisted job list shown first, settings below | Recovery state is visible before launching more work |
| Motion | Native state changes, no animation or compositor tricks | Keeps CPU/RAM available for media processing |

## Interaction promises

- Source/library selection does **not** trigger new frame reads; the explicit
  **Frames laden** action owns that cost. Screens without matching evidence
  never claim an independent verification from a still preview.
- Quality metrics are derived from existing `AlignmentResult`, and only the
  domain model decides if an automatic export is allowed. A visual match
  result is not cached as an export authorization.
- A new source/master, timeline position change or analysis invalidates old
  images. FFmpeg reads a capped 640px MJPEG still via file-only protocols and a
  timeout. A closing window cancels a pending read.
- Batch retry and resume preserve exactly the established validated journal
  and explicit overwrite/skip rules. Opening the UI cannot start processing.
- The current native desktop has a dark base and high-contrast option. A light
  palette is intentionally **not** simulated by random local recolorings;
  design and accessibility QA on real Qt platforms must precede such a mode.

## Screenshots and gates

Screenshots are reproducibly captured by `scripts/capture_studio_2.py` with
synthetic episode/anchor data and isolated `XDG_*` paths. They are a layout
artifact, never a media-correctness or GPU-performance benchmark.

- `docs/screenshots/studio-review-2.png`: episode, review plot, track inspector
- `docs/screenshots/studio-frames-2.png`: two explicit frame placeholders
- `docs/screenshots/studio-queue-2.png`: persisted queue surface

The independent real-device gates for stable 2.0 are in `docs/RELEASE_CHECKLIST.md`.
