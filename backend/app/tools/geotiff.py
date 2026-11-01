"""Minimal TIFF reader for the tiny rasters a WCS point query returns (a few pixels around the farm).

Why not a library: the project has no imaging or GDAL dependency, and a point query returns a few hundred bytes. Supported: tiled or
striped, 8/16/32-bit integer or 32-bit float samples, one band, no compression or zlib (Deflate), predictor 1 or 2. Anything else
raises TiffError, which the caller reports as an unusable response; it never guesses a value.
"""
from __future__ import annotations

import struct
import zlib


class TiffError(ValueError):
    pass


_SIZES = {1: 1, 3: 2, 4: 4, 5: 8, 12: 8}
_FMT = {1: "B", 3: "H", 4: "I", 12: "d"}


def _entries(b: bytes, bo: str) -> dict[int, list]:
    (first,) = struct.unpack(bo + "I", b[4:8])
    (n,) = struct.unpack(bo + "H", b[first:first + 2])
    out: dict[int, list] = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack(bo + "HHII", b[first + 2 + i * 12:first + 14 + i * 12])
        size = _SIZES.get(typ)
        if size is None or typ not in _FMT:
            continue  # rationals and strings are not needed
        raw = b[first + 10 + i * 12:first + 14 + i * 12] if cnt * size <= 4 else b[val:val + cnt * size]
        out[tag] = list(struct.unpack(bo + _FMT[typ] * cnt, raw[:cnt * size]))
    return out


# (bits per sample, SampleFormat) -> struct code. SampleFormat: 1 unsigned, 2 signed, 3 float.
_SAMPLE_CODES = {(8, 1): "B", (16, 1): "H", (16, 2): "h", (32, 1): "I", (32, 2): "i", (32, 3): "f"}
_NO_COMPRESSION, _ZLIB_CODES = 1, (8, 32946)


def _undo_predictor(line: list, bits: int, code: str) -> None:
    """Horizontal differencing (predictor 2): each sample is stored as the difference to its left neighbour."""
    mask, half = (1 << bits) - 1, 1 << (bits - 1)
    for c in range(1, len(line)):
        total = line[c - 1] + line[c]
        if code in "hi":
            line[c] = ((total + half) & mask) - half
        elif code in "BHI":
            line[c] = total & mask
        else:
            line[c] = total


def _paste_tile(grid: list[list[float]], samples: tuple, tile_w: int, tile_h: int, x0: int, y0: int, bits: int, code: str, predictor: int) -> None:
    height, width = len(grid), len(grid[0]) if grid else 0
    for r in range(tile_h):
        line = list(samples[r * tile_w:(r + 1) * tile_w])
        if len(line) < tile_w:
            break
        if predictor == 2:
            _undo_predictor(line, bits, code)
        y = y0 + r
        if y >= height:
            break
        for c in range(min(tile_w, width - x0)):
            grid[y][x0 + c] = line[c]


def read_tiff(b: bytes) -> list[list[float]]:
    """-> rows of sample values (row 0 first). Raises TiffError for anything outside the supported subset."""
    if b[:2] not in (b"II", b"MM") or len(b) < 8:
        raise TiffError("not a TIFF")
    bo = "<" if b[:2] == b"II" else ">"
    try:
        t = _entries(b, bo)
        width, height, bits = t[256][0], t[257][0], t[258][0]
        comp, predictor, fmt = t.get(259, [_NO_COMPRESSION])[0], t.get(317, [1])[0], t.get(339, [1])[0]
        if t.get(277, [1])[0] != 1 or comp not in (_NO_COMPRESSION, *_ZLIB_CODES) or predictor not in (1, 2) or bits not in (8, 16, 32):
            raise TiffError("unsupported TIFF layout")
        code = _SAMPLE_CODES.get((bits, fmt))
        if code is None:
            raise TiffError("unsupported sample type")
        bytes_per_sample = bits // 8
        tiled = 324 in t
        if tiled:
            tile_w, tile_h, offsets, counts = t[322][0], t[323][0], t[324], t[325]
            tiles_across = -(-width // tile_w)
        else:  # striped: a strip is a full-width tile
            tile_w, tile_h, offsets, counts = width, t.get(278, [height])[0], t[273], t[279]
            tiles_across = 1
        grid = [[0.0] * width for _ in range(height)]
        for idx, (offset, count) in enumerate(zip(offsets, counts)):
            raw = b[offset:offset + count]
            data = zlib.decompress(raw) if comp in _ZLIB_CODES else raw
            n = len(data) // bytes_per_sample
            samples = struct.unpack(bo + code * n, data[: n * bytes_per_sample])
            x0, y0 = ((idx % tiles_across) * tile_w, (idx // tiles_across) * tile_h) if tiled else (0, idx * tile_h)
            _paste_tile(grid, samples, tile_w, tile_h, x0, y0, bits, code, predictor)
        return grid
    except (KeyError, IndexError, struct.error, zlib.error) as e:
        raise TiffError(f"unreadable TIFF: {type(e).__name__}") from e
