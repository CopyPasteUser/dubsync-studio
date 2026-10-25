# DubSync Studio

> A desktop workflow for aligning dubbed audio or alternate-language video sources with a reference master.

![Python](https://img.shields.io/badge/Python-desktop_app-3776AB?logo=python&logoColor=white)
![FFmpeg](https://img.shields.io/badge/media-FFmpeg-007808?logo=ffmpeg&logoColor=white)
![Vibe coded](https://img.shields.io/badge/development-vibe--coded-8A2BE2)

> **Vibe-coded project:** the workflow design, diagnostics, testing material, and quality decisions are mine; AI-assisted development was used to implement and refine parts of the project.

## Project status

This repository currently contains the project documentation. The working application source and packaged releases still need to be uploaded before the commands below can be used unchanged.

## What it does

DubSync Studio analyzes a source containing the desired dub and aligns it to a chosen video master. It is designed for episodic batch work where timing, cuts, intros, recaps, or scene order can differ between releases.

## Planned and implemented workflow

1. Add a master video and one or more dubbed sources.
2. Match episode names and inspect detected media streams.
3. Analyze timing and scene correspondence.
4. Flag real content mismatches instead of forcing a bad alignment.
5. Preview or review the proposed synchronization.
6. Export the aligned result with a concise batch report.

## Highlights

- Batch processing for season folders
- Audio and scene alignment
- Detection of sources that contain genuinely different scenes
- Clear statuses: exported, skipped, failed, cancelled, and not started
- Compact diagnostic reports that do not flood the terminal
- Resume-friendly workflow for partially completed batches
- Reworked, task-oriented desktop UI
- Performance profiles for light, balanced, and maximum hardware usage

## Performance profiles

| Profile | Intended use | Behavior |
|---|---|---|
| Light | Older laptops and background work | Low parallelism and conservative resource use |
| Balanced | Everyday systems | Good throughput without monopolizing the machine |
| Maximum | Fast desktops and dedicated runs | Higher parallelism and aggressive analysis |

Profiles should adapt to available CPU threads, memory, storage speed, and encoder capability instead of relying on one fixed preset.

## Requirements

- Python 3.10 or newer
- FFmpeg and FFprobe available on `PATH`
- Enough free storage for temporary analysis data and exported media

Typical Debian/Ubuntu setup:

```bash
sudo apt update
sudo apt install python3 python3-venv ffmpeg
```

## Development launch

After the source is uploaded:

```bash
git clone https://github.com/CopyPasteUser/dubsync-studio.git
cd dubsync-studio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Exact entry points and platform-specific instructions will be updated when the application source is published.

## Troubleshooting principles

- A large offset is not automatically a synchronization failure.
- If the master and source show different scenes, the episode mapping or source material must be corrected first.
- Preserve the original files and export to a separate directory.
- Review a few dialogue-heavy and scene-transition points before accepting a full batch.

## Legal note

Use DubSync Studio only with media you are legally permitted to process. The project does not provide copyrighted video or audio.

## License

A license will be selected when the source is uploaded.