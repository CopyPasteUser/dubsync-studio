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
For a verified candidate, the dialog additionally requires selecting the
specific row, checking the approval box and pressing **Fassung zuordnen**.
Only the current project assignment changes, not source files or names. Save
the `.dubsync` project to retain it. The reassigned edition is excluded from
automatic batches and requires a fresh independent full-episode visual check
before a single export; shared intros and credits are insufficient.
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

The optional Rust library also accelerates bounded candidate/hypothesis scoring,
drift scoring and long-offset verification, with NumPy fallback if the native
library is unavailable or incompatible. On synthetic CI fixtures, long-offset
verification was measured at 13.50×, jittered affine scoring at 10.97× and a
simple affine case at only 1.04×. These are isolated-stage results, **not**
real-video export, NAS, or GPU throughput claims.

To compare the full FFmpeg matching pipeline with Rust and NumPy backends,
including decoding time and exact evidence parity, run:

```bash
./.venv/bin/python scripts/benchmark_match_pipeline.py --repeat 3 --json
```

Without source paths, the benchmark uses an isolated synthetic clip with an
independent intro. Pass `SOURCE MASTER` to benchmark representative files
read-only. Physical-device and real-library performance acceptance remain open.

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

### CPU decoder thread calibration

```bash
./run.sh --benchmark-threads "/path/to/episode.mkv" --benchmark-seconds 20 --json
```

The command reads a representative **local** video and does not change settings
or export anything. For episodes lasting 15 minutes or longer, it tests separate
beginning, middle and late windows. It alternates candidates three times,
requires identical frame fingerprints, at least 15% aggregate speed gain and
no window over 10% slower. Affinity/cgroup limits cap usable threads, and NAS
files are excluded to avoid expensive repeated network reads. The results
describe sampling, not full-export throughput.

In **Settings → Performance → CPU-Decoder automatisch einmessen**, run a
cancellable local calibration, inspect the measured gain and parity, choose
**Übernehmen** and then **Speichern**. The accepted custom setting is scoped to
the CPU model and available CPU budget; changing hardware or presets disables
it. Default decoder behavior and export safety remain unchanged.
