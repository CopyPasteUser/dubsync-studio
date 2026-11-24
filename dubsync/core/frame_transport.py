"""Bounded transport of complete raw video frames from a buffered pipe."""
from __future__ import annotations

from typing import BinaryIO, Iterator

FRAME_SIZE = 32 * 32
BATCH_FRAMES = 8


def frame_batches(stream: BinaryIO) -> Iterator[bytes]:
    """Emit available whole frames, without waiting to fill an entire batch.

    BufferedReader.read1 performs at most one underlying read; short reads
    are normal on pipes. Keep at most one partial frame between reads.
    A trailing partial frame is emitted so the caller rejects truncated media.
    """
    read = getattr(stream, "read1", None) or stream.read
    pending = b""
    while True:
        chunk = read(BATCH_FRAMES * FRAME_SIZE)
        if not chunk:
            if pending:
                yield pending
            return
        data = pending + chunk if pending else chunk
        end = len(data) - len(data) % FRAME_SIZE
        pending = data[end:]
        if end:
            yield data[:end]
