"""Default screenshot comparer: decode 8-bit non-interlaced PNGs and count differing pixels."""

import struct
import zlib

_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def decode(path):
    data, pos, idat, head = open(path, "rb").read(), 8, b"", None
    while data[:8] == b"\x89PNG\r\n\x1a\n" and pos < len(data):
        (size,), kind = struct.unpack(">I", data[pos : pos + 4]), data[pos + 4 : pos + 8]
        head = struct.unpack(">IIBBBBB", data[pos + 8 : pos + 21]) if kind == b"IHDR" else head
        idat += data[pos + 8 : pos + 8 + size] if kind == b"IDAT" else b""
        pos += 12 + size
    if not head or head[2] != 8 or head[6] or head[3] not in _CHANNELS:
        raise ValueError(f"unsupported or invalid PNG: {path.name}")
    width, height, bpp = head[0], head[1], _CHANNELS[head[3]]
    raw, stride, rows, prev = zlib.decompress(idat), width * bpp, [], bytearray(width * bpp)
    for y in range(height):
        ftype, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride):
            a, b, c = line[i - bpp] if i >= bpp else 0, prev[i], prev[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + (0, a, b, (a + b) // 2, _paeth(a, b, c))[ftype]) & 255
        rows.append(bytes(line))
        prev = line
    return width, height, bpp, rows


def diff_ratio(baseline, current):
    """Fraction of pixels that differ; 1.0 when the image sizes or formats differ."""
    (w, h, bpp, r1), (w2, h2, bpp2, r2) = decode(baseline), decode(current)
    if (w, h, bpp) != (w2, h2, bpp2):
        return 1.0
    changed = sum(a[x : x + bpp] != b[x : x + bpp] for a, b in zip(r1, r2) for x in range(0, w * bpp, bpp))
    return changed / (w * h) if w * h else 0.0
