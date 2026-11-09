# DubSync Studio · Roadmap to 3.0

Updated **2026-11-09**. Milestones below describe capability tracks, **not**
retroactively published version tags. `3.0.0b1` is a development beta; no
stable 2.x or 3.x release has been certified.

## Next feature and optimization sprints

Prioritized **2026-11-09**. The following sprints are separate from the physical
3.0 acceptance gates; an implemented feature is not automatically certified on
real media, GPUs or NAS mounts.

| Sprint | Priority | Status | Deliverable and acceptance criteria |
| --- | --- | --- | --- |
| **1. Headless inspection and RAM safety** | P1 | **Implemented; hardware QA pending** | CLI library scan of multiple roots, JSON grouping without export; queue count/progress overview without media paths; container-aware free-memory scheduling. Unit/integration tests pass; verify on real NAS and cgroup-limited hosts. |
| **2. Stable 3.0 acceptance** | P0 | **Open** | Wrong-episode / multi-cut real-media corpus, GPU execution + parity measurements, NAS disconnect/reconnect, clean installer/desktop checks. No stable tag until release checklist passes. |
| **3. Episode match explorer** | P1 | **Planned** | Ranked *visual* candidate evidence for ambiguous season editions, human-approved reassignment only, explicit no-match result, no confidence-gate bypass; baseline false-positive tests. |
| **4. Continuous A/B audio audition** | P2 | **Planned** | Bounded, synchronized playback/scrubbing with cancel, source selection and no silent export authorization; keyboard access and resource caps. |
| **5. Adaptive throughput tuning** | P2 | **Partially implemented** | Compare single/two-worker and CPU/GPU timings on local media, record speed, RAM and NAS I/O; adapt only worker/decoder resources, never sample density or export safety. |
| **6. Independent scene-boundary evidence** | P2 | **Planned** | Confirm complicated cuts using an independent visual signal; fail closed on disagreements, keep reproducible proof diagnostics and a reference corpus. |
| **7. Optional Rust engine experiment** | P3 | **Conditional** | Profile real episodes first; port only a measured Python-bound hotspot, compare correctness and end-to-end speed against existing NumPy/OpenCV, preserve Python/Qt fallback. |

Suggested order: complete the physical 3.0 acceptance gates, then the candidate
explorer and audition tools. Optimization work should be driven by recorded
pipeline timings, not hypothetical language speedups.

| Track | Status | Acceptance / outstanding work |
| --- | --- | --- |
| 2.0 · Reliable foundation | **Implemented; physical QA pending** | Native three-column studio, bounded image previews, cautious resume, atomic export. Test real process crashes, NAS loss and recovery before a stable label. |
| 2.1 · Deep review | **Implemented in 3.0 beta** | Measured image-evidence map, largest-gap jump navigation, on-demand A/B audio waveform excerpts, unchanged export gates. Next: usable continuous audio scrubbing and playback A/B. |
| 2.2 · Episode intelligence | **Partly implemented** | Explicit filename-identity checks and two-way independently confirmed swap recovery exist. Next: user-reviewable ranked candidate matrix and season-level mismatch explanations. No unilateral reassignments. |
| 2.3 · Adaptive engine | **Implemented; benchmark QA pending** | CPU/RAM-aware Light/Balanced/Maximum presets, separate Intel/AMD render-node discovery, verified frame parity and speed gate, UI decoder self-test. Next: repeatable reference-device benchmarks and sustained-load throttling. |
| 3.0 · Advanced matching | **Beta implementation** | Piecewise 2–5 independently proven monotone segments; disjoint scenes remain silent/skipped. New: exclude cross-segment repeated motifs from proof. Next: disagreement diagnostics from an independent visual signal and stronger mixed-cut/drift reference corpus. |
| **3.0 stable** | **Blocked by physical acceptance** | All criteria below must be met on actual hardware and NAS installations. |

## Required before stable 3.0

1. **Real-world correctness:** varied episode cuts, long introductions,
   missing/inserted scenes, variable drift, repeated credits and deliberately
   wrong episodes; verify that false matches never silently auto-export.
2. **Hardware qualification:** test the Intel iGPU (including older Intel HD),
   AMD VAAPI, NVIDIA CUDA where available, and plain CPU. Capture actual
   performance/resource use and whether the decode is physically accelerated.
3. **NAS failure drill:** disconnect/remount SMB/NFS during scan, analysis and
   write. Check cancellation, existing-file handling, private journal state,
   no source changes, and explicit safe resume.
4. **Cross-desktop QA:** representative XFCE and GNOME/KDE Linux systems,
   fractional scaling, keyboard-only use, error/empty/loading states, and
   desktop installer/uninstaller; document Windows limitations honestly.
5. **Release hygiene:** test suite, FFmpeg media export integration, packaging,
   clean install, GitHub Actions, backup/restore of user state, and release notes.

## Proposed follow-up features after 3.0

- **Episode match explorer:** compare several candidate editions against the
  same master in a read-only scorecard; require human approval for any reassignment.
- **Continuous A/B audition:** transport controls and synchronized audio
  scrubbing using bounded playback windows and explicit input selection.
- **More editing models:** robust outlier/drift visualization with an independent
  check against scene boundaries before proposing extra time-warp segments.
- **Runtime auto-tuning:** adaptive decoding concurrency based on measured
  throughput, available RAM, active background load and NAS I/O contention,
  while never changing visual safety thresholds.

## Non-negotiable invariants

- No source mutation, silent replacement, guessed missing dialogue or silent
  resumption of queued jobs.
- No automatic export based on waveform similarity, manual timeline edits,
  cached old alignment decisions, or repeated bookend scenes alone.
- Unavailable GPU, driver, network mount or decoder must have a clear, safe
  fallback/error state with bounded cancellation and diagnostic evidence.

The historical [2.0 design and gates](ROADMAP_2_0.md) remain available; stable
release verification is tracked in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
