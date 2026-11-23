"""Measured palettes.

Nothing here was designed.  Every hex value below was recovered from the
vector content of 349 figure PDFs shipped in the arXiv sources of 46 papers
from three groups (Tsinghua MARS, Wenzhao Zheng, Megvii), and kept only if it
appeared in at least four distinct figures.  See references/corpus-report.md.

The scan found three palettes coexisting in the corpus, because the authors
use three different drawing tools.  Mixing them is what makes a figure look
assembled rather than drawn, so each is kept whole and named:

  ``office``   Microsoft Office 2013+ theme accents.  The tint ladder you get
               from the PowerPoint/Visio colour picker.  Dominant in the
               corpus and the default here.
  ``drawio``   diagrams.net stock shape styles.  These ship as fill/stroke
               *pairs*, and the pairing is the whole point -- a draw.io box
               with an Office outline reads as wrong.
  ``tab10``    matplotlib's default cycle.  Only correct for plot-like panels
               (curves, scatter, bars) so that an inline chart matches the
               separately-generated result plots in the same paper.

Ladder positions are named rather than numbered so a spec can say
``tint: light`` and stay readable.
"""

# Ladder rungs, palest to deepest.  Not every family fills every rung; the
# resolver clamps.
RUNGS = ("pale", "light", "soft", "mid", "strong", "deep", "dark")


# ---------------------------------------------------------------- office
# Office 2013+ theme accents.  Rungs correspond to the "Lighter 80% / 60% /
# 40%", base, "Darker 25% / 50%" entries in the Office colour picker.
OFFICE = {
    #            pale      light     soft      mid       strong    deep      dark
    "blue":   ("#EAF1FA", "#DEEBF7", "#BDD7EE", "#9DC3E6", "#5B9BD5", "#2E75B6", "#1F4E79"),
    "navy":   ("#E9EEF8", "#DAE3F3", "#B4C7E7", "#8FAADC", "#4472C4", "#2F5597", "#264478"),
    "green":  ("#EFF6E9", "#E2F0D9", "#C5E0B4", "#A9D18E", "#70AD47", "#548235", "#375623"),
    "gold":   ("#FFF9E6", "#FFF2CC", "#FFE699", "#FFD966", "#FFC000", "#BF9000", "#997300"),
    "orange": ("#FDF2E9", "#FBE5D6", "#F8CBAD", "#F4B183", "#ED7D31", "#C55A11", "#843C0C"),
    "red":    ("#FCEAEA", "#F8D7D7", "#F2C2C2", "#E39B9B", "#C00000", "#A00000", "#700000"),
    "purple": ("#F2EDF6", "#E5DAEE", "#CBB4DD", "#B18FCC", "#7030A0", "#5A2680", "#431C60"),
    "teal":   ("#E6F6FC", "#CDEDFA", "#9BDBF5", "#68C9F0", "#00B0F0", "#10739E", "#0E5573"),
    "grey":   ("#F2F2F2", "#E7E6E6", "#D9D9D9", "#BFBFBF", "#A6A6A6", "#7F7F7F", "#595959"),
}

# Outline colour Office users actually pick per family.  Overwhelmingly the
# corpus outlines in black or grey (45% of stroke length is pure black); a
# family-tinted outline is the minority case, so this table is only consulted
# when a spec asks for ``outline: match``.
OFFICE_STROKE = {
    "blue": "#2E75B6", "navy": "#2F5597", "green": "#548235", "gold": "#BF9000",
    "orange": "#C55A11", "red": "#A00000", "purple": "#5A2680", "teal": "#10739E",
    "grey": "#7F7F7F",
}


# ---------------------------------------------------------------- drawio
# diagrams.net stock styles: (fill, stroke).  Both halves are mandatory.
DRAWIO = {
    "blue":   ("#DAE8FC", "#6C8EBF"),
    "green":  ("#D5E8D4", "#82B366"),
    "orange": ("#FFE6CC", "#D79B00"),
    "gold":   ("#FFF2CC", "#D6B656"),
    "red":    ("#F8CECC", "#B85450"),
    "purple": ("#E1D5E7", "#9673A6"),
    "grey":   ("#F5F5F5", "#666666"),
}


# ---------------------------------------------------------------- tab10
# matplotlib default property cycle, for plot panels only.
TAB10 = ("#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
         "#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF")

# Saturated singles the corpus reaches for when it wants a callout rather
# than a fill: a red highlight ring, a cyan "ours" curve.
SIGNAL = {
    "red": "#FF0000",        # 34 figures -- the standard error/highlight ring
    "crimson": "#C82423",
    "cyan": "#00B0F0",
    "jade": "#32B897",
    "amber": "#FFC000",
    "violet": "#7030A0",
}


def ladder(family, system="office"):
    """Return the tint ladder for *family* under the named palette system."""
    if system == "drawio":
        fill, stroke = DRAWIO.get(family, DRAWIO["grey"])
        # draw.io has no ladder; synthesise one so ``tint:`` still resolves.
        return (fill, fill, fill, _mix(fill, stroke, 0.25),
                _mix(fill, stroke, 0.55), stroke, _mix(stroke, "#000000", 0.35))
    return OFFICE.get(family, OFFICE["grey"])


def pick(family, tint="light", system="office"):
    lad = ladder(family, system)
    i = RUNGS.index(tint) if tint in RUNGS else 1
    return lad[min(i, len(lad) - 1)]


def outline(family, system="office"):
    if system == "drawio":
        return DRAWIO.get(family, DRAWIO["grey"])[1]
    return OFFICE_STROKE.get(family, "#7F7F7F")


def _mix(a, b, t):
    def ch(h, i):
        return int(h[1 + 2 * i:3 + 2 * i], 16)
    return "#%02X%02X%02X" % tuple(
        int(round(ch(a, i) * (1 - t) + ch(b, i) * t)) for i in range(3))


def families(system="office"):
    return sorted(DRAWIO if system == "drawio" else OFFICE)
