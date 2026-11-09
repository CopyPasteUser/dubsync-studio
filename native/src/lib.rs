//! Native monotone frame matching. No media I/O, no export decisions.

const GAP: f64 = 0.205;
const MAX_SAMPLES: usize = 5_000;

#[no_mangle]
pub extern "C" fn dubsync_native_abi() -> u32 {
    1
}

/// Pack a 32x32 frame's 8x8 DCT low-frequency block into the original 63-bit
/// perceptual hash (DC excluded). u64::MAX is an impossible hash and signals
/// invalid input. The DCT itself stays in optimized OpenCV native code.
///
/// # Safety
/// `coefficients` must reference at least 64 readable contiguous f32 values
/// when `length` is 64.
#[no_mangle]
pub unsafe extern "C" fn dubsync_phash_coefficients(
    coefficients: *const f32,
    length: usize,
) -> u64 {
    if coefficients.is_null() || length != 64 {
        return u64::MAX;
    }
    let coefficients = std::slice::from_raw_parts(coefficients, 64);
    if !coefficients.iter().all(|value| value.is_finite()) {
        return u64::MAX;
    }
    let mut values = [0f32; 63];
    values.copy_from_slice(&coefficients[1..]);
    values.select_nth_unstable_by(31, |a, b| a.total_cmp(b));
    let median = values[31];
    let mut hash = 0u64;
    for (bit, &value) in coefficients[1..].iter().enumerate() {
        if value >= median {
            hash |= 1u64 << bit;
        }
    }
    hash
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phash_preserves_median_and_little_endian_bit_positions() {
        let mut coeff = [0f32; 64];
        for (index, value) in coeff.iter_mut().enumerate() {
            *value = index as f32;
        }
        assert_eq!(unsafe { dubsync_phash_coefficients(coeff.as_ptr(), 64) },
                   ((1u64 << 63) - (1u64 << 31)));
        let constant = [0f32; 64];
        assert_eq!(unsafe { dubsync_phash_coefficients(constant.as_ptr(), 64) },
                   u64::MAX >> 1);
    }

    #[test]
    fn phash_rejects_invalid_buffers_and_nan_coefficients() {
        let mut coeff = [0f32; 64];
        assert_eq!(unsafe { dubsync_phash_coefficients(std::ptr::null(), 64) }, u64::MAX);
        assert_eq!(unsafe { dubsync_phash_coefficients(coeff.as_ptr(), 63) }, u64::MAX);
        coeff[1] = f32::NAN;
        assert_eq!(unsafe { dubsync_phash_coefficients(coeff.as_ptr(), 64) }, u64::MAX);
    }

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
}
