# DubSync Studio

**Synchronize multilingual video editions by their pictures, not their language.**

DubSync is a local Linux desktop app that compares visual fingerprints across
different editions of an episode, aligns the extra audio tracks, and builds a
multilingual MKV. It is designed for entire seasons, altered intros, missing
scenes, and media libraries on local disks or NAS mounts.

![DubSync Studio – visual fingerprint and audio timeline illustration](docs/media/dubsync-studio-hero.webp)

<sub>Concept artwork, not an application screenshot.</sub>

**Current release:** `3.0.0b1` · **Status:** beta · **Platform:** Linux desktop ·
**Stack:** Python / PySide6 / FFmpeg / OpenCV · **License:** MIT

## A look inside

| Visual evidence workbench | Audio A/B review |
| :---: | :---: |
| ![DubSync Studio timeline and evidence review](docs/screenshots/studio-review-3.png) | ![Two audio waveforms in the review panel](docs/screenshots/studio-waveform-3.png) |
| **Persistent batch queue** | **Hardware-aware presets** |
| ![Batch control room](docs/screenshots/studio-queue-3.png) | ![Adaptive performance settings](docs/screenshots/studio-hardware-presets-3.png) |

<sub>These are real Qt interface captures populated with **synthetic demonstration data**.
The waveforms are illustrative signals, not extracted from a real episode.
They do not demonstrate a completed export or measured GPU performance.</sub>

## What it does

- **Picture-based alignment:** monotone frame matching, drift correction, and
  conservative synchronization across separately verified scene segments.
- **Evidence you can inspect:** timeline markers, unverified-gap navigation,
  reasons for blocked exports, optional frame comparisons and short A/B audio
  waveforms. Audio previews never substitute for visual proof.
- **Safe multilingual export:** preserves existing video and tracks, verifies
  generated MKVs, and never silently overwrites or edits source files.
- **Season-scale processing:** episode-identity checks, guarded recovery of
  mutually verified episode swaps, private diagnostics, a persistent job queue,
  and explicit resume/retry.
- **Hardware-aware performance:** Light / Balanced / Maximum resource presets;
  Intel iGPU, AMD and NVIDIA decoder candidates are measured against a CPU
  reference on local videos. Unsupported, inaccurate or slower candidates fall
  back to CPU. A manual decoder self-test is available under System Diagnostics.

## Get started

Requires **Python 3.10+**, **FFmpeg**, **FFprobe**, and a Linux desktop. Install
the media tools through your distribution first.

```bash
git clone https://github.com/CopyPasteUser/dubsync-studio.git
cd dubsync-studio
./install-wizard.sh  # guided desktop installation
```

Or run it directly without installing a desktop launcher:

```bash
./setup.sh
./run.sh
```

For a headless environment check: `./run.sh --doctor`. See
[installation details](docs/INSTALL_LINUX.md) for distribution-specific notes.

## Workflow

1. Add folders containing the language editions and **scan** the library.
2. Select an episode and confirm its picture master and audio tracks.
3. Run visual analysis. Inspect confidence, **evidence gaps**, and the optional
   frame/audio comparisons before accepting a result.
4. Create a multilingual MKV or prepare a batch. Interrupted jobs stay in the
   queue until **you explicitly resume** them.

`Alt+1` Studio · `Alt+2` Queue · `Alt+3` Activity · `Ctrl+F` Episode search

## Correctness comes first

- Different episodes **must not** pass on matching intros or end cards alone.
  For cut segments, the matcher requires multiple independent visual motifs.
- Unmatched master scenes are **silent in the added audio track**; missing source
  scenes are not stretched to fabricate dialogue.
- Manually edited segments never become automatically verified. Low-confidence
  results require deliberate review and confirmation.
- Output files are staged and checked before atomic promotion; resume checks
  fresh source/output evidence instead of trusting an old success flag.
- Media analysis and diagnostics run locally. This is not a cloud service.

## 3.0 status

`3.0.0b1` adds evidence-gap navigation, bounded waveform previews, a GUI
CPU/GPU parity self-test, and stronger independent proof for multi-cut matching.
**It is not a stable 3.0 release yet.** Physical Intel/AMD/NVIDIA runs,
real NAS disconnect/recovery, and wider Linux/Windows usability tests remain
open release gates. GPU benchmarking verifies *output equivalence and speed*,
not actual utilization of GPU silicon.

Read the [roadmap to stable 3.0](docs/ROADMAP_NEXT.md),
[release checklist](docs/RELEASE_CHECKLIST.md),
[architecture](docs/ARCHITECTURE.md), [security notes](SECURITY.md), or
[changelog](CHANGELOG.md).

---

Built with AI-assisted development and human-directed product decisions.
Created by [CopyPasteUser](https://github.com/CopyPasteUser). MIT licensed.
