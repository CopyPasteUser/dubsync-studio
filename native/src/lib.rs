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
}
