from __future__ import annotations

from typing import BinaryIO


def read_bounded(stream: BinaryIO, max_bytes: int, chunk_size: int = 8192) -> tuple[bytes, bool]:
    data = bytearray()
    truncated = False
    while True:
        remaining = max_bytes - len(data)
        if remaining <= 0:
            truncated = True
            break
        chunk = stream.read(min(chunk_size, remaining + 1))
        if not chunk:
            break
        if len(chunk) > remaining:
            data.extend(chunk[:remaining])
            truncated = True
            break
        data.extend(chunk)
    return bytes(data), truncated
