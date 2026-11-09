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
| **3. Episode match explorer** | P1 | **Read-only explorer implemented; reassignment open** | Up to eight explicit candidates ranked by full visual matching and stricter independent interior episode proof; desktop dialog and JSON CLI, no-match and infrastructure-error distinctions, false-positive regression tests. Any future human-approved reassignment needs a separate identity-safe workflow; no gate bypass. |
| **4. Continuous A/B audio audition** | P2 | **Bounded A/B transport implemented; long-form stream pending** | 12-second reference-aligned A/B windows with in-app Qt playback/pause/stop/scrub, up to four private cached clips for quick switching, bounded cancellable FFmpeg and external-player fallback. Seamless long-form streaming across multiple windows and physical audio-device QA remain open. |
| **5. Adaptive throughput tuning** | P2 | **Reproducible worker comparison implemented; autotuning pending** | New `--benchmark-workers SOURCE MASTER` measures actual 1-vs-2 decoder sampling time, verifies exact visual-anchor and export-decision parity, reports effective NAS/resource policy; no setting is changed. Next: sustained CPU/GPU/NAS trials with RAM/I/O telemetry, then guarded autotuning of decoder resources only. |
| **6. Independent scene-boundary evidence** | P2 | **Planned** | Confirm complicated cuts using an independent visual signal; fail closed on disagreements, keep reproducible proof diagnostics and a reference corpus. |
| **7. Optional Rust engine experiment** | P3 | **Implemented; physical QA pending** | Monotone DP hot path ported with zero external crates, parity tests and benchmark; Python/Qt and NumPy fallback preserved. Confirm end-to-end speed/packaging on reference hardware before making native mandatory. |
| **8. Optional subtitles** | P2 | **Implemented; provider/media QA pending** | Extra toggleable SRT track in individual and batch MKV outputs, original video copied; fast local checks, online SubDL + Subliminal search (ranked, bounded multi-candidate retry), manually supplied yt-dlp video URL, and Whisper as last fallback or an explicit find-only mode. Added optional int8 batched CTranslate2 inference scaled to free RAM. Subsequent runs reuse only validated online/ASR results via a private 128 MiB / 256-entry cache with source-fingerprint, duration, provider and model invalidation, separate expiry and an opt-out. Test real episode cut/timing, subtitle release mismatches, API keys/quotas, ASR CPU/GPU speed and accuracy, NAS failure and player controls before release qualification. |

Suggested order: complete the physical 3.0 acceptance gates, then the candidate
explorer and audition tools. Optimization work should be driven by recorded
pipeline timings, not hypothetical language speedups.

| Track | Status | Acceptance / outstanding work |
| --- | --- | --- |
| 2.0 · Reliable foundation | **Implemented; physical QA pending** | Native three-column studio, bounded image previews, cautious resume, atomic export. Test real process crashes, NAS loss and recovery before a stable label. |
| 2.1 · Deep review | **Implemented in 3.0 beta** | Measured image-evidence map, largest-gap jump navigation, on-demand A/B waveforms and in-app 12-second A/B player with seek/stop/pause. Next: long-form seamless listening and physical audio hardware acceptance. Export gates unchanged. |
| 2.2 · Episode intelligence | **Partly implemented** | Explicit filename-identity checks and two-way independently confirmed swap recovery, plus a read-only candidate scorecard (desktop and CLI). Next: approved identity-safe reassignment workflow and season-level matrix. No unilateral reassignments. |
| 2.3 · Adaptive engine | **Implemented; benchmark QA pending** | CPU/RAM-aware profiles, separate Intel/AMD render-node discovery, verified frame parity and speed gate, UI decoder self-test, plus headless 1-vs-2 decoder benchmark with anchor parity. Next: real-device trials and sustained-load autotuning. |
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
- **Long-form A/B audition:** optionally stream successive bounded windows
  without a manual re-request; keep discontinuous cuts silent and cancellation
  immediate. Current 12-second A/B window and scrubbing are implemented.
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
