# DubSync Studio — advanced workflows

[Back to README](../README.md) · [Installation](INSTALL_LINUX.md) · [Architecture](ARCHITECTURE.md)

## Headless library and queue inspection

The same installation can inspect a library or queued jobs without starting Qt:

```bash
./run.sh --scan-library "/mnt/nas/series/de" --scan-library "/mnt/nas/series/en"
./run.sh --scan-library "/mnt/nas/series" --json
./run.sh --queue-status --json
```

Scanning groups editions by filename and folder metadata only; it never
decodes, synchronizes or exports media, and grouping is **not** visual proof
that two files contain the same episode. JSON scan output contains full local
file paths, so keep it private. Queue status reports counts and recent progress,
without source paths or stored failure logs; it does not start or resume jobs.

## Episode candidate explorer (visual evidence)

In the Studio inspector, select a picture master and click **Kandidaten …**
to choose up to eight other videos. DubSync ranks them by actual frame alignment,
requiring stronger evidence from separate interior episode sections before
labeling a candidate verified. Shared intros or credits alone do not qualify.
The result is **inspection-only**: nothing is reassigned, renamed, or exported.
Disconnected NAS files and decoder failures are reported as errors, **not** as
negative visual matches. On large libraries, review a small candidate set at a
time; the first pass decodes video and can take several minutes.

The same report is available without Qt:

```bash
./run.sh --explore-episode "/mnt/media/master.mkv" "/mnt/media/dub1.mkv" "/mnt/media/dub2.mkv" --json
```

Candidate indexes in the report refer to the order supplied on the command
line, even when the scorecard sorts the results. A unique verified candidate
is a review hint, **never** an export or reassignment approval.

## Optional Rust frame matcher

The video-frame sequence matcher can run in a small Rust library while all
media decoding, synchronization safety checks and the Qt interface remain
unchanged. With Rust/Cargo installed, `./setup.sh` builds it automatically;
the manual command is `./build-native.sh`. If it is absent or incompatible,
DubSync uses the original NumPy matcher. Set `DUBSYNC_NATIVE=0` before launching
to force NumPy for comparison. `--doctor` reports the active availability.
The Rust build has no third-party crates or network runtime requirement.
**Packaging note:** Universal Python wheels never bundle a platform-specific
Rust `.so`/`.dylib`/`.dll` under the misleading `py3-none-any` tag. Wheel-only
installations use the safe NumPy backend; source/desktop installations can build
their native matcher with `./build-native.sh`. CI checks wheel contents.

`./.venv/bin/python scripts/benchmark_native.py` runs a deterministic 1200-frame
matcher-only speed comparison. A faster matcher does **not** imply the same
end-to-end gain: video decoding, NAS reads and FFmpeg export remain separate costs.

## Measure the best decoder concurrency on your machine

For the same source/master pair, measure serial against two-worker video
sampling before changing the performance preset:

```bash
./run.sh --benchmark-workers "/mnt/media/dub.mkv" "/mnt/media/master.mkv" --json
```

This **runs the full visual analysis twice** without exporting; it records the
actual decoder worker count, measured sampling time, and exact anchor/safety
parity. A 15% measured speed improvement with two *effective* workers is needed
before it suggests two. NAS and low-resource restrictions still apply, and no
settings are changed. Disk page caches, network traffic and other background
load can skew a single comparison; repeat it on representative local media.
