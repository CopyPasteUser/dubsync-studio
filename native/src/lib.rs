//! Native monotone frame matching. No media I/O, no export decisions.

const GAP: f64 = 0.205;
const MAX_SAMPLES: usize = 5_000;

#[no_mangle]
pub extern "C" fn dubsync_native_abi() -> u32 {
    1
}

/// Write ascending `(source_index, target_index)` pairs into a u32 buffer.
/// Returns pair count, or a negative value for invalid/bounded inputs.
///
/// # Safety
/// The caller must supply valid readable input arrays and a writable output
/// buffer of at least `2 * min(source_len, target_len)` u32 values.
#[no_mangle]
pub unsafe extern "C" fn dubsync_match(
    source: *const u64,
    source_len: usize,
    target: *const u64,
    target_len: usize,
    output: *mut u32,
    output_len: usize,
) -> isize {
    if source_len > MAX_SAMPLES || target_len > MAX_SAMPLES {
        return -1;
    }
    if source_len == 0 || target_len == 0 {
        return 0;
    }
    if source.is_null() || target.is_null() || output.is_null()
        || output_len < 2 * source_len.min(target_len)
    {
        return -1;
    }

    let sources = std::slice::from_raw_parts(source, source_len);
    let targets = std::slice::from_raw_parts(target, target_len);
    let out = std::slice::from_raw_parts_mut(output, output_len);
    let width = target_len + 1;
    // Two f32 rows instead of an O(n*m) cost matrix. One byte per trace cell;
    // distances are recomputed during traceback, never kept as another matrix.
    let mut previous: Vec<f32> = (0..width).map(|j| j as f32 * GAP as f32).collect();
    let mut current = vec![0f32; width];
    let mut trace = vec![0u8; (source_len + 1) * width];
    for i in 1..=source_len {
        current[0] = i as f32 * GAP as f32;
        for j in 1..=target_len {
            let distance = (sources[i - 1] ^ targets[j - 1]).count_ones();
            let diagonal = f64::from(previous[j - 1]) + f64::from(distance) / 64.0;
            let above = f64::from(previous[j]) + GAP;
            let left = f64::from(current[j - 1]) + GAP;
            let (best, direction) = if diagonal <= above && diagonal <= left {
                (diagonal, 1)
            } else if above <= left {
                (above, 2)
            } else {
                (left, 3)
            };
            current[j] = best as f32;
            trace[i * width + j] = direction;
        }
        std::mem::swap(&mut previous, &mut current);
    }

    let mut pairs = Vec::with_capacity(source_len.min(target_len));
    let (mut i, mut j) = (source_len, target_len);
    while i > 0 && j > 0 {
        match trace[i * width + j] {
            1 => {
                if (sources[i - 1] ^ targets[j - 1]).count_ones() <= 18 {
                    pairs.push(((i - 1) as u32, (j - 1) as u32));
                }
                i -= 1;
                j -= 1;
            }
            2 => i -= 1,
            3 => j -= 1,
            _ => return -2,
        }
    }
    for (index, (src, tgt)) in pairs.iter().rev().enumerate() {
        out[2 * index] = *src;
        out[2 * index + 1] = *tgt;
    }
    pairs.len() as isize
}

/// Find at most two closest targets for every source, ordered by (Hamming, target index).
/// Writes triples `(source_index, target_index, distance)` to `output`.
/// Returns the number of triples; negative values indicate invalid bounds/pointers.
///
/// # Safety
/// All nonempty input arrays must be readable for their respective lengths.
/// `output` must have room for at least `6 * source_len` u32 values. The
/// caller must not alias writable output storage with either input array.
#[no_mangle]
pub unsafe extern "C" fn dubsync_feature_candidates(
    source: *const u64,
    source_len: usize,
    target: *const u64,
    target_len: usize,
    output: *mut u32,
    output_len: usize,
) -> isize {
    if source_len > MAX_SAMPLES || target_len > MAX_SAMPLES {
        return -1;
    }
    if source_len == 0 || target_len == 0 {
        return 0;
    }
    if source.is_null() || target.is_null() || output.is_null() || output_len < 6 * source_len {
        return -1;
    }
    let sources = std::slice::from_raw_parts(source, source_len);
    let targets = std::slice::from_raw_parts(target, target_len);
    let out = std::slice::from_raw_parts_mut(output, output_len);
    let mut count = 0usize;
    for (source_index, &source_hash) in sources.iter().enumerate() {
        let mut first = (17u32, usize::MAX);
        let mut second = first;
        for (target_index, &target_hash) in targets.iter().enumerate() {
            let distance = (source_hash ^ target_hash).count_ones();
            if distance > 16 {
                continue;
            }
            let candidate = (distance, target_index);
            if candidate < first {
                second = first;
                first = candidate;
            } else if candidate < second {
                second = candidate;
            }
        }
        for (distance, target_index) in [first, second] {
            if target_index != usize::MAX {
                let position = count * 3;
                out[position] = source_index as u32;
                out[position + 1] = target_index as u32;
                out[position + 2] = distance;
                count += 1;
            }
        }
    }
    count as isize
}

/// Return the six closest permitted targets for each source frame, ordered by
/// (Hamming distance, target index), excluding distances above 18.
/// Output triples are `(source_index, target_index, Hamming distance)`.
///
/// # Safety
/// `source` and `target` must be readable for their declared lengths;
/// `target_allowed` must contain at least `target_len` readable bytes; and
/// `output` must contain at least `18 * source_len` writable u32 values.
/// The writable output must not overlap input storage.
#[no_mangle]
pub unsafe extern "C" fn dubsync_offset_candidates(
    source: *const u64,
    source_len: usize,
    target: *const u64,
    target_len: usize,
    target_allowed: *const u8,
    output: *mut u32,
    output_len: usize,
) -> isize {
    if source_len > MAX_SAMPLES || target_len > MAX_SAMPLES {
        return -1;
    }
    if source_len == 0 || target_len == 0 {
        return 0;
    }
    if source.is_null() || target.is_null() || target_allowed.is_null() || output.is_null()
        || output_len < 18 * source_len
    {
        return -1;
    }
    let sources = std::slice::from_raw_parts(source, source_len);
    let targets = std::slice::from_raw_parts(target, target_len);
    let allowed = std::slice::from_raw_parts(target_allowed, target_len);
    let out = std::slice::from_raw_parts_mut(output, output_len);
    let mut count = 0usize;
    for (source_index, &source_hash) in sources.iter().enumerate() {
        let mut best = [(19u32, usize::MAX); 6];
        for (target_index, &target_hash) in targets.iter().enumerate() {
            if allowed[target_index] == 0 {
                continue;
            }
            let distance = (source_hash ^ target_hash).count_ones();
            if distance > 18 {
                continue;
            }
            let candidate = (distance, target_index);
            if let Some(position) = best.iter().position(|&current| candidate < current) {
                best.copy_within(position..5, position + 1);
                best[position] = candidate;
            }
        }
        for (distance, target_index) in best {
            if target_index == usize::MAX {
                break;
            }
            let position = count * 3;
            out[position] = source_index as u32;
            out[position + 1] = target_index as u32;
            out[position + 2] = distance;
            count += 1;
        }
    }
    count as isize
}

fn median_f64(values: &mut [f64]) -> f64 {
    let middle = values.len() / 2;
    let (_, upper, _) = values.select_nth_unstable_by(middle, f64::total_cmp);
    let upper = *upper;
    if values.len() % 2 == 0 {
        let lower = values[..middle].iter().copied().max_by(f64::total_cmp).unwrap();
        (lower + upper) / 2.0
    } else {
        upper
    }
}

/// Score a bounded, already sorted chunk of affine hypotheses against at most
/// two feature candidates per source. The output is
/// `(matched_count, -median_hamming, -median_residual, slope, offset)`.
/// A return of zero means no supported hypothesis; one means success.
///
/// # Safety
/// All input buffers must be readable for their declared lengths. `hypotheses`
/// contains `2 * hypothesis_len` f64 values. `output` must contain five writable
/// f64 values and must not alias any input. Nonempty inputs cannot be null.
#[no_mangle]
pub unsafe extern "C" fn dubsync_best_affine(
    source_indices: *const u32,
    target_indices: *const u32,
    distances: *const u8,
    source_times: *const f64,
    target_times: *const f64,
    candidate_len: usize,
    source_len: usize,
    hypotheses: *const f64,
    hypothesis_len: usize,
    tolerance: f64,
    output: *mut f64,
    output_len: usize,
) -> isize {
    const MAX_HYPOTHESES_PER_CALL: usize = 256;
    if source_len > MAX_SAMPLES || candidate_len > 2 * source_len
        || hypothesis_len > MAX_HYPOTHESES_PER_CALL || !tolerance.is_finite()
        || tolerance <= 0.0 || output_len < 5
    {
        return -1;
    }
    if candidate_len == 0 || hypothesis_len == 0 {
        return 0;
    }
    if source_indices.is_null() || target_indices.is_null() || distances.is_null()
        || source_times.is_null() || target_times.is_null()
        || hypotheses.is_null() || output.is_null()
    {
        return -1;
    }
    let source_indices = std::slice::from_raw_parts(source_indices, candidate_len);
    let target_indices = std::slice::from_raw_parts(target_indices, candidate_len);
    let distances = std::slice::from_raw_parts(distances, candidate_len);
    let source_times = std::slice::from_raw_parts(source_times, candidate_len);
    let target_times = std::slice::from_raw_parts(target_times, candidate_len);
    let hypotheses = std::slice::from_raw_parts(hypotheses, hypothesis_len * 2);
    let out = std::slice::from_raw_parts_mut(output, output_len);

    let mut first = vec![usize::MAX; source_len];
    let mut second = vec![usize::MAX; source_len];
    let mut previous_source = 0usize;
    for (i, &source) in source_indices.iter().enumerate() {
        let src = source as usize;
        if src >= source_len || (i > 0 && src < previous_source)
            || !source_times[i].is_finite() || !target_times[i].is_finite()
        {
            return -1;
        }
        if first[src] == usize::MAX {
            first[src] = i;
        } else if second[src] == usize::MAX {
            second[src] = i;
        } else {
            return -1;
        }
        previous_source = src;
    }

    let mut best: Option<(usize, f64, f64, f64, f64)> = None;
    let mut selected_distances = Vec::with_capacity(source_len);
    let mut selected_residuals = Vec::with_capacity(source_len);
    for hypothesis in hypotheses.chunks_exact(2) {
        let (slope, offset) = (hypothesis[0], hypothesis[1]);
        if !slope.is_finite() || !offset.is_finite() {
            return -1;
        }
        selected_distances.clear();
        selected_residuals.clear();
        let mut highest_target: Option<u32> = None;
        for src in 0..source_len {
            let a = first[src];
            if a == usize::MAX {
                continue;
            }
            let residual_a = (target_times[a] - (slope * source_times[a] + offset)).abs();
            let valid_a = residual_a <= tolerance;
            let b = second[src];
            let residual_b = if b != usize::MAX {
                (target_times[b] - (slope * source_times[b] + offset)).abs()
            } else {
                f64::INFINITY
            };
            let valid_b = residual_b <= tolerance;
            let selected = if valid_a && (!valid_b || distances[a] < distances[b]
                || (distances[a] == distances[b] && residual_a <= residual_b))
            {
                Some((a, residual_a))
            } else if valid_b {
                Some((b, residual_b))
            } else {
                None
            };
            if let Some((index, residual)) = selected {
                let target = target_indices[index];
                if highest_target.map_or(true, |previous| target > previous) {
                    highest_target = Some(target);
                    selected_distances.push(distances[index] as f64);
                    selected_residuals.push(residual);
                }
            }
        }
        if selected_distances.is_empty() {
            continue;
        }
        let count = selected_distances.len();
        let median_distance = -median_f64(&mut selected_distances);
        let median_residual = -median_f64(&mut selected_residuals);
        let score = (count, median_distance, median_residual);
        if best.map_or(true, |(n, d, r, _, _)| score > (n, d, r)) {
            best = Some((count, median_distance, median_residual, slope, offset));
        }
    }
    if let Some((count, distance, residual, slope, offset)) = best {
        out[..5].copy_from_slice(&[count as f64, distance, residual, slope, offset]);
        1
    } else {
        0
    }
}

/// Find monotone best matches in a local timestamp window for one offset.
/// Writes `(source_index, target_index, hamming_distance)` triples.
///
/// # Safety
/// Each hash and timestamp pointer must reference its declared array length.
/// `output` must contain `3 * source_len` writable u32 values and must not
/// alias either source or target. Target timestamps must be nondecreasing.
#[no_mangle]
pub unsafe extern "C" fn dubsync_verify_offset(
    source_hashes: *const u64,
    source_times: *const f64,
    source_len: usize,
    target_hashes: *const u64,
    target_times: *const f64,
    target_len: usize,
    shift: f64,
    tolerance: f64,
    output: *mut u32,
    output_len: usize,
) -> isize {
    if source_len > MAX_SAMPLES || target_len > MAX_SAMPLES
        || output_len < 3 * source_len || !shift.is_finite()
        || !tolerance.is_finite() || tolerance <= 0.0
    {
        return -1;
    }
    if source_len == 0 || target_len == 0 {
        return 0;
    }
    if source_hashes.is_null() || source_times.is_null() || target_hashes.is_null()
        || target_times.is_null() || output.is_null()
    {
        return -1;
    }
    let source_hashes = std::slice::from_raw_parts(source_hashes, source_len);
    let source_times = std::slice::from_raw_parts(source_times, source_len);
    let target_hashes = std::slice::from_raw_parts(target_hashes, target_len);
    let target_times = std::slice::from_raw_parts(target_times, target_len);
    let out = std::slice::from_raw_parts_mut(output, output_len);
    if source_times.iter().any(|t| !t.is_finite()) || target_times.iter().any(|t| !t.is_finite())
        || target_times.windows(2).any(|w| w[0] > w[1])
    {
        return -1;
    }
    let mut matches = 0usize;
    let mut next_target = 0usize;
    for (source_index, (&hash, &time)) in source_hashes.iter().zip(source_times).enumerate() {
        let predicted = time + shift;
        let j = target_times.partition_point(|&timestamp| timestamp < predicted);
        let start = next_target.max(j.saturating_sub(2));
        let end = target_len.min(j.saturating_add(3));
        let mut best: Option<(u32, f64, usize)> = None;
        for target_index in start..end {
            let phase = (target_times[target_index] - predicted).abs();
            if phase > tolerance {
                continue;
            }
            let distance = (hash ^ target_hashes[target_index]).count_ones();
            if distance > 18 {
                continue;
            }
            let candidate = (distance, phase, target_index);
            if best.map_or(true, |old| candidate < old) {
                best = Some(candidate);
            }
        }
        if let Some((distance, _, target_index)) = best {
            let index = matches * 3;
            out[index] = source_index as u32;
            out[index + 1] = target_index as u32;
            out[index + 2] = distance;
            matches += 1;
            next_target = target_index + 1;
        }
    }
    matches as isize
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_frames_preserve_order() {
        let frames = [2u64, 4, 8, 16, 32, 64];
        let mut output = [0u32; 12];
        let count = unsafe {
            dubsync_match(frames.as_ptr(), 6, frames.as_ptr(), 6, output.as_mut_ptr(), 12)
        };
        assert_eq!(count, 6);
        assert_eq!(output, [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]);
    }

    #[test]
    fn invalid_lengths_and_null_pointers_are_rejected() {
        let frames = [1u64];
        let mut output = [0u32; 2];
        assert_eq!(unsafe {
            dubsync_match(frames.as_ptr(), 5_001, frames.as_ptr(), 1, output.as_mut_ptr(), 2)
        }, -1);
        assert_eq!(unsafe {
            dubsync_match(std::ptr::null(), 1, frames.as_ptr(), 1, output.as_mut_ptr(), 2)
        }, -1);
        assert_eq!(unsafe {
            dubsync_match(frames.as_ptr(), 1, frames.as_ptr(), 1, output.as_mut_ptr(), 1)
        }, -1);
        assert_eq!(unsafe {
            dubsync_match(std::ptr::null(), 0, std::ptr::null(), 0, std::ptr::null_mut(), 0)
        }, 0);
    }

    #[test]
    fn candidate_scan_prefers_distance_then_earliest_target() {
        let source = [0u64, 7u64, u64::MAX];
        let target = [1u64, 0u64, 0u64, 7u64];
        let mut output = [999u32; 18];
        let count = unsafe {
            dubsync_feature_candidates(source.as_ptr(), 3, target.as_ptr(), 4,
                                       output.as_mut_ptr(), output.len())
        };
        assert_eq!(count, 4);
        assert_eq!(&output[..12], &[0, 1, 0, 0, 2, 0, 1, 3, 0, 1, 0, 2]);
    }

    #[test]
    fn candidate_scan_rejects_invalid_buffers() {
        let source = [0u64];
        let mut output = [0u32; 6];
        assert_eq!(unsafe {
            dubsync_feature_candidates(source.as_ptr(), 5_001, source.as_ptr(), 1,
                                       output.as_mut_ptr(), output.len())
        }, -1);
        assert_eq!(unsafe {
            dubsync_feature_candidates(std::ptr::null(), 1, source.as_ptr(), 1,
                                       output.as_mut_ptr(), output.len())
        }, -1);
        assert_eq!(unsafe {
            dubsync_feature_candidates(source.as_ptr(), 1, source.as_ptr(), 1,
                                       output.as_mut_ptr(), 5)
        }, -1);
        assert_eq!(unsafe {
            dubsync_feature_candidates(std::ptr::null(), 0, std::ptr::null(), 0,
                                       std::ptr::null_mut(), 0)
        }, 0);
    }

    #[test]
    fn offset_candidates_preserve_ties_and_ignore_repeated_targets() {
        let source = [0u64, u64::MAX];
        let target = [0u64, 1, 0, 2, 4, 8, 16, 32, u64::MAX];
        let allowed = [1u8, 1, 0, 1, 1, 1, 1, 1, 1];
        let mut output = [999u32; 36];
        let count = unsafe {
            dubsync_offset_candidates(source.as_ptr(), 2, target.as_ptr(), target.len(),
                                      allowed.as_ptr(), output.as_mut_ptr(), output.len())
        };
        assert_eq!(count, 7);
        assert_eq!(&output[..21], &[0, 0, 0, 0, 1, 1, 0, 3, 1,
                                    0, 4, 1, 0, 5, 1, 0, 6, 1, 1, 8, 0]);
    }

    #[test]
    fn offset_candidates_reject_invalid_bounds() {
        let frames = [0u64];
        let allowed = [1u8];
        let mut output = [0u32; 18];
        assert_eq!(unsafe {
            dubsync_offset_candidates(frames.as_ptr(), 5001, frames.as_ptr(), 1,
                                      allowed.as_ptr(), output.as_mut_ptr(), 18)
        }, -1);
        assert_eq!(unsafe {
            dubsync_offset_candidates(frames.as_ptr(), 1, frames.as_ptr(), 1,
                                      std::ptr::null(), output.as_mut_ptr(), 18)
        }, -1);
        assert_eq!(unsafe {
            dubsync_offset_candidates(frames.as_ptr(), 1, frames.as_ptr(), 1,
                                      allowed.as_ptr(), output.as_mut_ptr(), 17)
        }, -1);
        assert_eq!(unsafe {
            dubsync_offset_candidates(std::ptr::null(), 0, std::ptr::null(), 0,
                                      std::ptr::null(), std::ptr::null_mut(), 0)
        }, 0);
    }

    #[test]
    fn affine_hypotheses_rank_by_matches_then_median_with_deterministic_ties() {
        let source_indices = [0u32, 1, 2, 3];
        let target_indices = [0u32, 1, 2, 3];
        let distances = [1u8, 0, 0, 2];
        let source_times = [0.0f64, 1.0, 2.0, 3.0];
        let target_times = [3.0f64, 4.0, 5.0, 6.0];
        let hypotheses = [1.0f64, 0.0, 1.0, 3.0, 1.0, 3.0];
        let mut output = [0.0f64; 5];
        let result = unsafe {
            dubsync_best_affine(
                source_indices.as_ptr(), target_indices.as_ptr(), distances.as_ptr(),
                source_times.as_ptr(), target_times.as_ptr(), 4, 4,
                hypotheses.as_ptr(), 3, 0.85, output.as_mut_ptr(), 5,
            )
        };
        assert_eq!(result, 1);
        assert_eq!(output, [4.0, -0.5, 0.0, 1.0, 3.0]);
    }

    #[test]
    fn affine_rejects_more_than_two_candidates_per_source() {
        let indices = [0u32, 0, 0];
        let distances = [0u8, 0, 0];
        let times = [0.0f64, 0.0, 0.0];
        let hypotheses = [1.0f64, 0.0];
        let mut output = [0.0f64; 5];
        let result = unsafe {
            dubsync_best_affine(
                indices.as_ptr(), indices.as_ptr(), distances.as_ptr(),
                times.as_ptr(), times.as_ptr(), 3, 2,
                hypotheses.as_ptr(), 1, 0.85, output.as_mut_ptr(), 5,
            )
        };
        assert_eq!(result, -1);
    }

    #[test]
    fn offset_verifier_finds_best_monotone_targets() {
        let hashes = [5u64, 10, 15, 20];
        let times = [0.0f64, 1.0, 2.0, 3.0];
        let target_hashes = [100u64, 5, 10, 15, 20];
        let target_times = [0.0f64, 3.0, 4.0, 5.0, 6.0];
        let mut output = [0u32; 12];
        let count = unsafe {
            dubsync_verify_offset(
                hashes.as_ptr(), times.as_ptr(), hashes.len(),
                target_hashes.as_ptr(), target_times.as_ptr(), target_hashes.len(),
                3.0, 0.9, output.as_mut_ptr(), output.len(),
            )
        };
        assert_eq!(count, 4);
        assert_eq!(output, [0, 1, 0, 1, 2, 0, 2, 3, 0, 3, 4, 0]);
    }

    #[test]
    fn offset_verifier_rejects_invalid_bounds() {
        let hash = [0u64];
        let time = [0.0f64];
        let mut output = [0u32; 3];
        assert_eq!(unsafe {
            dubsync_verify_offset(hash.as_ptr(), time.as_ptr(), 1,
                                  hash.as_ptr(), time.as_ptr(), 1,
                                  0.0, 0.85, output.as_mut_ptr(), 2)
        }, -1);
        assert_eq!(unsafe {
            dubsync_verify_offset(std::ptr::null(), time.as_ptr(), 1,
                                  hash.as_ptr(), time.as_ptr(), 1,
                                  0.0, 0.85, output.as_mut_ptr(), 3)
        }, -1);
    }
}
