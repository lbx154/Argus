"""Pixel dimensions of PNG / JPEG / GIF, with no dependencies.

Needed so an ``image:`` node can inherit the real aspect ratio of the photo
it points at.  Getting this wrong is the loudest tell in a generated figure:
squashed input frames.
"""

import struct


def size_bytes(blob):
    """Like ``size`` for an in-memory file (an image inside a .pptx)."""
    import io
    return _size_fh(io.BytesIO(blob), None)


def size(path):
    """Return (width, height) in pixels, or None if the format is unknown."""
    try:
        with open(path, "rb") as fh:
            return _size_fh(fh, path)
    except Exception:
        return None


def _size_fh(fh, path):
    try:
        if True:
            head = fh.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head[:3] == b"GIF":
                w, h = struct.unpack("<HH", head[6:10])
                return w, h
            if head[:2] == b"\xff\xd8":
                return _jpeg(fh)
            if head[:4] == b"%PDF" and path:
                return _pdf(path)
            if head[:2] in (b"II", b"MM"):
                return _tiff(fh)
    except Exception:
        return None
    return None


def _tiff(fh):
    fh.seek(0)
    head = fh.read(8)
    end = "<" if head[:2] == b"II" else ">"
    off = struct.unpack(end + "I", head[4:8])[0]
    fh.seek(off)
    n = struct.unpack(end + "H", fh.read(2))[0]
    w = h = None
    for _ in range(n):
        tag, typ, cnt, val = struct.unpack(end + "HHI4s", fh.read(12))
        v = struct.unpack(end + ("H" if typ == 3 else "I"), val[:2 if typ == 3 else 4])[0]
        if tag == 256:
            w = v
        elif tag == 257:
            h = v
    return (w, h) if w and h else None


def _jpeg(fh):
    fh.seek(2)
    while True:
        b = fh.read(1)
        while b and b != b"\xff":
            b = fh.read(1)
        while b == b"\xff":
            b = fh.read(1)
        if not b:
            return None
        marker = b[0]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        ln = struct.unpack(">H", fh.read(2))[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            fh.read(1)
            h, w = struct.unpack(">HH", fh.read(4))
            return w, h
        fh.seek(ln - 2, 1)


def _pdf(path):
    """MediaBox of the first page, in points, for vector assets."""
    import re
    with open(path, "rb") as fh:
        blob = fh.read(400000)
    m = re.search(rb"/MediaBox\s*\[\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)", blob)
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return abs(x1 - x0), abs(y1 - y0)


def aspect(path, default=1.33):
    wh = size(path)
    if not wh or not wh[1]:
        return default
    return wh[0] / float(wh[1])
