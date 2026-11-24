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

## Local partial verification

Six standalone transport tests passed, including real FFmpeg output equality.
A local alternating six-run-per-variant transport microbenchmark with 4,000
32x32 grayscale frames (synthetic lavfi testsrc2, one FFmpeg thread) produced
identical byte digests. Median emitted messages: 4,000 before, 1,876.5 after
(range 1,436–2,625). Median process-plus-pipe time: 0.1998 s before, 0.1893 s
after. This is a transport-only measurement; it excludes production queue,
pHash, video decoding of real compressed inputs, matching and export. Do not
extrapolate it to application throughput.

A separate exploratory 1,800-pair cache payload microbenchmark found binary
packing faster than JSON by less than one millisecond per encode/decode
operation. Existing cache format is deliberately unchanged: migration and
validation complexity are not justified without real warm-cache profiling.
Full engine integration tests and target hardware qualification remain open.
