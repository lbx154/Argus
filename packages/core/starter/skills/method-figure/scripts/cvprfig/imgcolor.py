"""How much of a picture is painted in a given colour.

A class legend (car, bus, vegetation ...) explains colours that occur only
inside embedded renders, never on a vector shape, so "no shape uses this
fill" does not make such an entry an orphan.  The auditors look into the
pictures before they say so.

Occupancy, segmentation and point-cloud renders are shaded, so a pixel counts
for a legend colour when it is that colour times a brightness factor between
0.45 and 1.2 (Lambert shading, height tints), within a tolerance of 20 levels.
Calibrated on the qualitative figure of a CVPR submission (24 renders,
nuScenes-lidarseg palette): every one of the ten legend classes covers at
least 0.2 % of some render, while colours absent from the palette stay at or
below 0.006 %.  Grey and pale colours match backgrounds and anti-aliasing
everywhere, so only saturated legend colours are looked for.

Decoding needs Pillow or PyMuPDF.  Without either ``available()`` is False
and callers report that instead of guessing.
"""

import io

SHADE = (0.45, 1.2)
TOL = 20.0
MIN_SAT = 0.3
USED = 0.001          # 0.1 % of one picture's pixels
MAX_SIDE = 400


def _pil():
    try:
        from PIL import Image
        return Image
    except Exception:
        return None


def _fitz():
    try:
        import fitz
        return fitz
    except Exception:
        return None


def available():
    return _pil() is not None or _fitz() is not None


def rgb(col):
    """'#RRGGBB' -> (r, g, b), or None."""
    s = (col or "").lstrip("#")
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def saturated(c):
    hi, lo = max(c), min(c)
    return hi > 0 and (hi - lo) / float(hi) >= MIN_SAT


def histogram(src):
    """``(pixels, [(count, (r, g, b)), ...])`` of a picture given as bytes or a
    path, subsampled (nearest neighbour, so no new colours) to MAX_SIDE.
    Transparent pixels count as white.  None if it cannot be decoded."""
    Image = _pil()
    if Image is not None:
        try:
            im = Image.open(io.BytesIO(src) if isinstance(src, bytes) else src)
            im.load()
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            im = im.convert("RGB")
            k = max(1, -(-max(im.size) // MAX_SIDE))
            if k > 1:
                im = im.resize((max(1, im.width // k), max(1, im.height // k)), Image.NEAREST)
            n = im.width * im.height
            return n, im.getcolors(n)
        except Exception:
            pass
    fitz = _fitz()
    if fitz is not None:
        try:
            pix = fitz.Pixmap(src)
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            if pix.colorspace is None or pix.colorspace.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            while max(pix.width, pix.height) > MAX_SIDE * 2:
                pix.shrink(1)
            cc = pix.color_count(colors=True)
            return pix.width * pix.height, [(n, tuple(c[:3])) for c, n in cc.items()]
        except Exception:
            pass
    return None


def share(hist, c):
    """Fraction of the picture's pixels drawn in colour ``c`` or a shade of it."""
    n, cols = hist
    cc = float(sum(x * x for x in c)) or 1.0
    lo, hi = SHADE
    hit = 0
    for count, p in cols:
        k = min(max(sum(a * b for a, b in zip(p, c)) / cc, lo), hi)
        if sum((a - k * b) ** 2 for a, b in zip(p, c)) <= TOL * TOL:
            hit += count
    return hit / float(n) if n else 0.0


def best_share(hists, c):
    return max((share(h, c) for h in hists if h), default=0.0)


class Pictures(object):
    """Colour histograms of a figure's pictures, decoded on first use.
    ``sources`` are paths or bytes; a subclass may override ``read``."""

    def __init__(self, sources=()):
        self.sources = list(sources)
        self._hists = None

    def __bool__(self):
        return bool(self.sources)

    __nonzero__ = __bool__

    def read(self, src):
        return src

    def hists(self):
        if self._hists is None:
            self._hists = [h for h in (histogram(self.read(s)) for s in self.sources) if h]
        return self._hists


def lookup(col, pics, assume=False):
    """Whether a legend colour that no shape uses is drawn in the pictures:
    True when ``assume``d, ``(found, best share)`` after looking, None when
    the pictures cannot be decoded, or False when there is nothing to look
    at -- no pictures, or a grey or pale colour that backgrounds would match
    anyway."""
    c = rgb(col)
    if not pics or c is None or not saturated(c):
        return False
    if assume:
        return True
    if not available():
        return None
    s = best_share(pics.hists(), c)
    return s >= USED, s
