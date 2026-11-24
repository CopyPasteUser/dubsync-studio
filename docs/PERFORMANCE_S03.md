# S03 — Batch and export hot paths (incremental)

The first independent S03 change preserves every batch journal save and atomic
replacement, schema, Unicode string, and resume proof. It serializes compact
JSON once instead of streaming indented encoder fragments. Human-readable
summaries and parsed JSON consumers remain unchanged; whitespace in the raw
journal file changes. Non-finite numbers are still rejected. A temporary
serialization string increases transient memory versus streaming json.dump;
measure very large real batches before increasing journal size limits.

For affine audio alignment only, omit the adelay filter when the computed
integer millisecond delay is exactly zero. Nonzero delay, atempo, resampling,
trim, padding, piecewise audio, AAC encoding and output verification remain
unchanged. This is not lossless audio stream copying.

## Local partial verification

Four isolated tests passed, including 12 PCM parity subcases (44.1/48 kHz,
mono/stereo, tempo .98/1/1.02), using real FFmpeg and identical raw output.
The journal test checks decoded JSON equality including Unicode and resume
proof, and rejects non-finite values. No application-wide integration result
is implied.

Synthetic serialization benchmark: 100 episode records, each with 40
diagnostic events; ten alternating measurements per implementation using
StringIO for old json.dump. Old median 23.855 ms, new 3.119 ms; UTF-8 output
763136 vs 246124 bytes, parsed objects exactly equal. These are serialization
measurements, not filesystem durability or overall batch throughput.

## Remaining gates

Run the full batch diagnostics/resume/export/Qt regression suite and compare
S01 baseline reports. Test real local/NAS jobs, cancellation and crash recovery.
The bounded multi-episode scheduler and optional exact-timestamp audio stream
copy remain unimplemented pending resource and packet-timestamp validation;
do not weaken the existing AAC verification contract. No CI workflows changed.

## Bounded metadata scheduling

When the existing two-worker sampling profile is selected, prefetch missing
metadata for the current eligible episode with at most two existing FFprobe
workers. Reuse the current CPU/RAM resource guard, forbid NAS parallelism and
publish results only after a complete successful probe. Sequential mode and
preflight/skip order remain unchanged. This is not concurrent episode export.
Four isolated scheduler contracts passed; real batch integration and throughput
measurements are still required.
