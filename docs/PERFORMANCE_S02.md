# S02 — Media engine optimization (incremental)

This branch is independent of the S01 benchmark PR. It changes the FFmpeg
transport, not FFmpeg's sample grid, filter graph, pHash, alignment thresholds,
cache format or export authorization. Buffered pipe reads return up to eight
frames per queue message; fragmented reads are assembled into exact frames,
with incomplete final frames still rejected. Cancellation is checked between
fingerprints, and the queue remains bounded (two messages, up to eight frames
each). This reduces potential Python queue wakeups, not necessarily total
decode time; it does not claim a hardware speedup.

CPU/GPU qualification now receives the same decoder thread cap as the full
sampling pass. Per-file parity/speed checks, NAS exclusion, multi-window
verification and runtime CPU fallback are retained.

## Verification
Run the existing FFmpeg sampling, parallel sampling, frame cache, alignment,
native parity and full release suites. Compare S01 cold/warm baseline reports
on the same media and host. Include a real GPU test before declaring decoder
performance improved. Further cache changes, native hashing and GPU-native
scaling remain benchmark-driven follow-ups; do not change the fingerprint
format or safety evidence without exact parity and a cache migration plan.
