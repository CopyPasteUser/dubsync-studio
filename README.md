# DubSync Studio

**Synchronize multilingual video editions by their pictures, not their language.**

A Linux desktop app that visually matches different cuts of an episode, aligns additional audio and exports a verified multilingual MKV. Works with local disks and mounted NAS libraries.

![DubSync Studio concept illustration](docs/media/dubsync-studio-hero.webp)

<sub>Concept artwork, not an application screenshot.</sub>

**Release:** `3.0.0b1` (beta) · **Stack:** Python, PySide6, FFmpeg, OpenCV, optional Rust · **License:** MIT

| Visual evidence | Batch queue |
| :---: | :---: |
| ![Review timeline](docs/screenshots/studio-review-3.png) | ![Batch queue](docs/screenshots/studio-queue-3.png) |

<sub>UI screenshots use synthetic demonstration data, not proven media exports.</sub>

## Features

- **Visual synchronization:** frame matching, scene-level evidence, drift correction and detection of missing or changed segments.
- **Review tools:** timeline, evidence gaps, frame comparisons and short A/B audio previews.
- **Safe MKV export:** keeps the original video and existing tracks, verifies the output and does not overwrite sources.
- **Season workflows:** episode identity checks, persistent queue and explicit resume/retry.
- **Performance:** Light/Balanced/Maximum presets; optional Rust matching and measured CPU/GPU decoder fallbacks.
- **Optional subtitles:** source existing online or local captions, or transcribe locally as a last resort.

## Get started

Requires **Linux, Python 3.10+, FFmpeg and FFprobe**.

```bash
git clone https://github.com/CopyPasteUser/dubsync-studio.git
cd dubsync-studio
./install-wizard.sh
```

To run without installing a desktop launcher:

```bash
./setup.sh
./run.sh
```

Run `./run.sh --doctor` for a headless environment check. [Linux installation guide](docs/INSTALL_LINUX.md).

## Workflow

1. Scan folders containing different language editions.
2. Choose a picture master and review the matching episode candidates.
3. Analyze visual alignment and inspect gaps or low-confidence matches.
4. Export a multilingual MKV, or queue episodes for later processing.

**Subtitles are optional, switchable player tracks—not burned-in captions.** Automatic lookup checks local/embedded subtitles, then searches online using Subliminal and SubDL (where available), and only then attempts local Whisper transcription. A **Find only** mode never runs AI. Video-URL subtitle extraction and ZIP sources are also available. Existing video stays untouched without re-encoding.

[Subtitle sources, providers and setup](docs/SUBTITLES.md) · [Headless scans, candidate explorer, Rust matcher and benchmarking](docs/ADVANCED_WORKFLOWS.md)

## Safety and status

DubSync refuses unverified episode matches, does not fabricate dialogue for missing scenes and requires deliberate review of uncertain alignments. Exports use staging and verification; source files are never silently overwritten. Media analysis runs locally; optional online subtitle searches contact external providers only when selected.

**3.0 is still beta.** Physical decoder/GPU testing, NAS failure recovery and broader desktop acceptance remain open.

[Roadmap](docs/ROADMAP_NEXT.md) · [Release checklist](docs/RELEASE_CHECKLIST.md) · [Architecture](docs/ARCHITECTURE.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

Created by [CopyPasteUser](https://github.com/CopyPasteUser) with AI-assisted development and human-directed product decisions. MIT licensed.
