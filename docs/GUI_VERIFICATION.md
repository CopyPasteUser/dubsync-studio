# DubSync Studio 2.0.0b2 development verification

## Automated checks (2026-10-29)

- Full `unittest discover -s tests -q` suite, including core, batch,
  GUI/offscreen, installer and synthetic media fixtures. The automated suite
  runs without a real NAS or a physical AMD/Intel/NVIDIA GPU.
- Segment editor Qt tests cover selection, split, enable/disable, invalid
  overlap, undo/redo of structural and timing edits, and confirmation on
  discard. Changes never bypass the manual-export safety flag.
- Batch resume tests cover completed/exported outputs, changed source content,
  changed output content and incompatible export recipes. Invalid or incomplete
  journal evidence cannot grant a skip as "verified".
- GPU hint tests cover a full hybrid AMD/Intel comparison, reuse scoped by
  codec with fresh CPU/selected-GPU verification, and fallback after a failed
  preferred candidate.
- Job queue tests verify private SQLite persistence, active PID protection on
  POSIX, explicit recovery, journal-first persistence, failed-job visibility,
  recipe preservation, safe problem-only retries and the automatic CPU/RAM budget.
  A two-episode fixture interrupts the first run and resumes the second, without
  altering the already completed output.
- 2.0 workbench tests verify explicit-only frame reads, missing mapped scenes,
  unverified nominal times, timeline cursor/zoom keys, and queue UI actions
  that never auto-start when merely listing persisted jobs.
- `scripts/capture_studio_2.py` produces four native Qt screenshots from
  **clearly labelled synthetic data** in an isolated temporary settings/state
  tree, never from a real NAS or user project.
- The fourth `studio-hardware-presets-2.png` screenshot shows the hardware
  preset control and its generated CPU/GPU preview; it is not evidence of a
  physically tested Intel/AMD adapter.
- `python3 scripts/release_check.py --require-gui 1` validates metadata,
  source syntax, FFmpeg/FFprobe availability and PySide6 dependency.
- Strict frontend static audit reports zero findings. Its rules primarily
  target web apps, so this is **not** a Qt desktop accessibility certification.
- The Hallmark web/CSS-specific design linter is not a meaningful validator for
  the native PySide6 stylesheet: `sloplint --scope app` exits with "no .html,
  .css, .jsx, .tsx, .vue, .svelte or .astro files found", not a pass/fail
  result for this Qt project. Manual screenshot review and Qt offscreen tests
  are used instead, with tokens centralized in `gui/theme.py`.

## Manual acceptance still required

- Real Qt display (not only offscreen), Windows and Linux window manager,
  keyboard-only flow, different scaling and available screen readers, including
  the new timeline, two-pane frame tab and queue action focus order.
- Actual GPU decoding throughput and selected adapter usage on Intel/AMD/NVIDIA,
  including integrated GPUs, codec variants and driver failure recovery.
- NAS read behavior, intermittent connectivity, permissions and interrupted
  transfers; NAS was unavailable during this development pass.
- Interrupted full-season batches with verified MKV outputs; changed outputs
  and incompatible journal configurations; no silent replacements.
- Native installers on all supported distributions and Windows installation
  variants; inspect actual desktop-entry icons and shortcuts.

The official release gate list is in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)
and the planned 2.0 milestones in [ROADMAP_2_0.md](ROADMAP_2_0.md).
