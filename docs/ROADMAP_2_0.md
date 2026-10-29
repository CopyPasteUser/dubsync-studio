# DubSync Studio: Roadmap to 2.0

Status: live development roadmap, **not** a promised release date. The current
source reports **2.0.0b2**. The complete native workbench exists; beta is not
synonymous with a tested stable release. Hardware, NAS faults, installers and
real desktop UX remain open release gates.

## Development stages

| Stage | Main implementation | Exit criterion |
| --- | --- | --- |
| 1.5.x (completed) | Codec-scoped, revalidated GPU preference; explicit, checked batch resumption; segment-editor undo/redo and inspection; release tests | Unit + offscreen GUI tests |
| 1.6 beta (implemented) | Persistent job queue with explicit pause/restart recovery, output provenance and resumed partial seasons; conservative load-based decoder concurrency | Synthetic restart/recovery tests passed; physical kill/restart, NAS and real Intel/AMD/NVIDIA tests remain open |
| 1.7 candidate (incorporated into 2.0 beta) | Unified episode inspector, keyboard navigation and visible synchronization review; legacy source/track widgets retained | Qt automated checks; Linux/Windows user testing still open |
| 2.0 beta (implemented) | Three-pane native studio, measured-anchor timeline, side-by-side on-demand frame review, in-page persistent queue control, diagnostics, revised docs | Offline Qt smoke checks and failure-state tests; real-data UX/performance gates still open |
| 2.0 stable (pending) | Installer/update QA, measured cross-platform behavior, backwards-compatible projects/migration, performance budgets, load/resume fault tests | Release checklist fully satisfied on physical targets |

## 2.0 interface: implemented versus still planned

1. **Implemented – unified workspace:** sources and episodes left, two-mode
   synchronization review center, track/segment inspector right. Three independent
   scroll owners and 980×680 minimum layout. The selected episode remains intact
   when switching to queue and diagnostics.
2. **Implemented – review before export:** real anchor distribution, coverage,
   confidence and unverified regions. Explicit provisional/nominative labels
   prevent stills being misread as independent safety proof. Original export
   constraints remain untouched.
3. **Implemented – timeline and stills:** cursor, zoom, keyboard seek and
   measured anchors with piecewise segments; source/master image comparison on
   manual request. **Future:** waveform overlays, prefetching where explicitly
   allowed, and richer visual drag editing when reliable non-destructive
   validation is available. The existing segment editor already has undo/redo.
4. **Implemented – batch control room:** direct persisted job state and selective
   retry; existing job dock handles pause/cancel. Queue reruns still use verified
   journals and explicit authorization. **Future:** per-episode drill-down and
   queue priorities with journal compatibility checks.
5. **Implemented in b2 – adaptive hardware resources:** Leicht/Mittel/Stark adapt
   CPU and worker budgets to physical/logical CPU, affinity/cgroups and RAM;
   the frame/codec analysis recipe remains independent. Auto recommends one
   preset and lists Intel-iGPU, AMD and NVIDIA decoder candidates for per-file
   verification. Queue workers also react to current load and available RAM.
   **Pending before stable:** physical Intel/AMD/NVIDIA decoder measurements,
   driver failure matrices and iGPU/dGPU throughput validation.
6. **Implemented – graphite native visual system:** Hallmark-inspired content
   hierarchy, three-pane console and restrained signals. Existing high-contrast
   toggle and focus styles retained. **Future:** independent light appearance
   if validated as a coherent token variant, UX QA at 125–200% scaling and
   assistive-technology evaluation on real desktop sessions.

## Release gates and unresolved verification

- **Correctness:** wrong episodes never merge; a match based on shared titles,
  intros or credits cannot bypass visual-coverage thresholds.
- **Data integrity:** source media is never modified; unfinished or stale output
  is not overwritten without explicit permission; project data can migrate.
- **Performance:** measure cold/warm runs for local SSD, HDD, SMB/NFS and both
  discrete and integrated GPUs. A visible GPU request does **not** establish
  hardware execution. Record actual throughput, CPU, RAM and I/O.
- **Resumption:** the persistent queue uses checked journals and skips *completed* outputs only
  when filename, path, recipe, source fingerprints, output fingerprint and
  video/audio probe agree. The quick signatures cover five distributed windows,
  not every media byte. An in-progress episode is revalidated rather than presumed complete.
- **Desktop quality:** Linux Mint/XFCE, other supported desktop environments,
  Windows and representative display sizes tested on real machines. Automated
  offscreen tests are useful, not a substitute for this validation.
- **NAS:** unavailable during this implementation pass; no live NAS performance
  or recovery claim is made. Revisit this gate when the NAS can be used.

## Timing

The first professional UI overhaul was **implemented in 2.0.0b1 on 2026-10-29**.
There is no trustworthy calendar date yet for the **stable 2.0.0**: the NAS
was offline during development and the Intel/AMD/NVIDIA/Windows real-device
gates cannot be inferred from synthetic tests. Once test hardware and media
are available, a focused verification pass and fixes are required before removing
the beta marker.
