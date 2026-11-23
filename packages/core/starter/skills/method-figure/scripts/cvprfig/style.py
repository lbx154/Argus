"""House style for top-tier CV/ML conference pipeline figures.

Every constant here is a measurement, not a preference.  The corpus is 349
figure PDFs from the arXiv sources of 46 papers by three groups whose figures
this project is trying to sound like -- Tsinghua MARS, Wenzhao Zheng's group,
and Megvii -- plus the CVPR/ICCV/ICLR/AAAI sample used for v1.  Provenance and
the exact numbers are in references/corpus-report.md.

Two findings drive almost everything below.

1.  Not one figure in the corpus is drawn in TikZ.  All 292 are external
    PDF/PNG artwork placed with \\includegraphics, and the giveaway fonts
    (Calibri, Cambria Math, Segoe Print) say the tool was PowerPoint, Visio or
    diagrams.net.  So the palette is the Office/draw.io colour picker, and
    matching it is the difference between "drawn by a person" and "generated".

2.  The corpus uses colour freely inside boxes but hardly ever on their
    outlines.  Excluding the page backdrop, the median diagram has 87% of its
    filled box area in a saturated family -- so "keep it mostly grey" would be
    wrong advice.  What is consistent is the *number* of families (median 5,
    75th percentile 9) and the outlines: 45% of stroke length in the corpus is
    pure black and most of the rest is the grey ladder, with family-tinted
    outlines a clear minority.  Hence ``outline: ink`` is the default here.
"""

from . import palettes

# --------------------------------------------------------------------------
# Palette.  Backed by palettes.py, which holds the measured hex values.
# ``FAMILIES`` is kept as a plain dict of ladders for backwards compatibility
# with v1 specs and with code that indexes it directly.
# --------------------------------------------------------------------------
SYSTEM = "office"                      # office | drawio  (see palettes.py)

FAMILIES = dict(palettes.OFFICE)
FAMILIES["steel"] = FAMILIES["navy"]   # v1 alias
FAMILIES["rose"] = FAMILIES["red"]     # v1 alias
TINT_INDEX = {name: i for i, name in enumerate(palettes.RUNGS)}

INK = "#000000"          # 45% of stroke length in the corpus is pure black
MUTED_INK = "#595959"    # secondary captions, axis-style annotation
FRAME_GREY = "#A6A6A6"   # dashed stage containers
SOFT_FRAME = "#BFBFBF"
PAPER = "#FFFFFF"
SIGNAL = dict(palettes.SIGNAL)

# Semantic roles.  Keeping roles stable across every figure in a paper is the
# single strongest signal of a hand-crafted figure set.  Defaults favour the
# blue/green/orange triad, which carries 72% of the coloured area in the
# corpus, and follow the convention the template papers (Occ3D, SparseWorld,
# BEVDepth) share: blue for what comes in and is encoded, green for what comes
# out, warm orange for the paper's own module, grey for everything inherited.
# v3 moved ``core``/``ours`` from gold to orange and ``decoder`` from purple
# to green: a pale-purple decoder next to a gold core is the combination
# image models default to, and it read as generated in review.
ROLES = {
    "input":      ("grey",   "light"),
    "backbone":   ("navy",   "light"),
    "encoder":    ("blue",   "light"),
    "decoder":    ("green",  "light"),
    "attention":  ("blue",   "soft"),
    "core":       ("orange", "light"),      # the paper's contribution
    "temporal":   ("gold",   "light"),
    "memory":     ("red",    "light"),
    "loss":       ("red",    "soft"),
    "output":     ("green",  "soft"),
    "baseline":   ("grey",   "light"),
    "ours":       ("orange", "light"),
    "aux":        ("teal",   "pale"),
    "frozen":     ("blue",   "pale"),
    "trainable":  ("orange", "pale"),
    "neutral":    ("grey",   "pale"),
}

# --------------------------------------------------------------------------
# Typography.  Sizes are in *final rendered points* -- the renderer scales the
# canvas so that these land verbatim on the printed page.
#
# v2.0 calibrated these on the 46-paper corpus (rendered median 6.0 pt, upper
# quartile 7.5 pt) and set the body tier at 7.0.  Figures built that way
# printed at 5.8-7.3 pt and read as small, so v3 recalibrated on the papers
# whose figures the users actually hold up as the target, measured the same
# way (glyph size in the figure PDF x the \includegraphics scale):
#
#     SparseWorld overview (Visio)      p10 6.4  median 7.5
#     SparseWorld comparison (Visio)    p10 6.1  median 6.6  p90 7.5
#     Occ3D pipeline (PowerPoint)                median 8.2
#     BEVDepth backbone (Keynote)                median 8.6
#     OccFusion teaser (PowerPoint)              median 6.4
#
# The body tier is now the SparseWorld median, 7.5 pt; stage titles 8.5 pt.
# The floor moves to 6.0 pt and the warning tier to 6.5 pt, and no module
# name may print under 7.0 pt.  Supervisor-Skills' figure checklist asks for
# 8 pt after scaling, which the stage titles meet and module names approach.
# --------------------------------------------------------------------------
FONT_STACKS = {
    "times":     "Times New Roman, Nimbus Roman, Liberation Serif, Times, serif",
    "helvetica": "Helvetica Neue, Helvetica, Arial, Liberation Sans, sans-serif",
    "arial":     "Arial, Liberation Sans, Helvetica, sans-serif",
}
# Serif is the corpus default: Times/Nimbus Roman/CMR carry 71% of characters
# in the diagram set, against 15% for Arial/Helvetica/Calibri.  Figures match
# the body font of the paper they sit in.
DEFAULT_FONT = "times"

TYPE = {
    "stage_title": {"size": 8.5, "weight": "bold", "style": "italic"},
    "panel_title": {"size": 8.5, "weight": "bold", "style": "normal"},
    "node":        {"size": 7.5, "weight": "normal", "style": "normal"},
    "node_strong": {"size": 7.5, "weight": "bold", "style": "normal"},
    "caption":     {"size": 7.0, "weight": "normal", "style": "normal"},
    "annot":       {"size": 6.5, "weight": "normal", "style": "italic"},
    "math":        {"size": 7.5, "weight": "normal", "style": "italic"},
    "tiny":        {"size": 6.0, "weight": "normal", "style": "normal"},
}
BASE_TYPE = {k: dict(v) for k, v in TYPE.items()}
MIN_RENDERED_PT = 6.0     # below this the auditor errors
WARN_RENDERED_PT = 6.5    # below this it warns
MIN_MODULE_PT = 7.0       # a module name is never smaller than this

# Every key in a spec that holds a font size.  ``type_scale`` multiplies all
# of them, so a figure that needs bigger type is re-laid-out from the spec
# rather than enlarged label by label in PowerPoint, where ``wrap="none"``
# text boxes would simply grow out of their modules.
SIZE_KEYS = ("size", "title_size", "caption_size", "label_size", "badge_size")


class scope(object):
    """Context manager: apply one figure's type scale and role overrides to
    the module tables for the duration of a build or an export, then put
    them back.  Re-entrant, so a writer may call into Figure.render."""

    depth = 0

    def __init__(self, k=1.0, *role_maps):
        self.k = float(k or 1.0)
        self.roles = {}
        for m in role_maps:
            for name, val in (m or {}).items():
                fam, _, tint = str(val).partition(".")
                self.roles[name] = (fam, tint or "light")

    def __enter__(self):
        scope.depth += 1
        if scope.depth == 1:
            self._saved = dict(ROLES)
            for key, base in BASE_TYPE.items():
                TYPE[key]["size"] = round(base["size"] * self.k, 3)
            ROLES.update(self.roles)
        return self

    def __exit__(self, *exc):
        scope.depth -= 1
        if scope.depth == 0:
            for key, base in BASE_TYPE.items():
                TYPE[key]["size"] = base["size"]
            ROLES.clear()
            ROLES.update(self._saved)
        return False


# --------------------------------------------------------------------------
# Strokes, in final rendered points.  v1 guessed these roughly 2x too heavy.
# Rendered-width modes in the corpus, after applying each figure's own scale
# factor: 0.19 pt (13%), 0.36 pt (24%), 0.45 pt (8%), 0.80 pt (5%), with a
# hairline shelf at 0.17-0.20.  Fine lines are the norm; a 1 pt outline is a
# deliberate shout.  ``flow`` moved 0.45 -> 0.60 in v3 so arrows keep their
# weight next to the larger 7.5 pt labels.
# --------------------------------------------------------------------------
STROKE = {
    "hairline": 0.19,
    "thin":     0.28,
    "box":      0.36,
    "flow":     0.60,
    "frame":    0.36,
    "emphasis": 0.80,
    "shout":    1.10,
}
DASH = {
    "frame":   "3.2,2.2",
    "region":  "2.6,1.8",
    "flow":    "3.0,2.0",
    "thin":    "1.6,1.4",
    "dot":     "0.8,1.4",
}

# Geometry, in final rendered points unless noted.
GEOM = {
    "corner":        2.6,    # module box corner radius
    "frame_corner":  5.0,    # dashed stage container radius
    "node_padx":     7.0,    # horizontal text padding inside a box
    "node_pady":     4.2,    # vertical text padding
    "line_gap":      1.18,   # multiline leading multiplier
    "gap_x":         16.0,   # default horizontal gap between siblings
    "lane":          4.0,    # spacing between parallel wires in one corridor
    "gap_y":         9.0,    # default vertical gap between siblings
    "stage_gap":     14.0,   # gap between stage containers
    "stage_pad":     8.0,    # padding inside a stage container
    "stage_title_gap": 4.0,  # gap between stage title baseline and container
    "arrow_len":     3.97,   # arrowhead length -- see ARROW_PRESET below
    "arrow_wid":     3.97,   # arrowhead width
    "slab_depth":    3.6,    # 3D extrusion depth for tensor slabs
    "iso_dx":        3.2,    # isometric offset, x
    "iso_dy":        2.4,    # isometric offset, y
    "port_stub":     9.9,    # = max(2.5 x arrow_len, 8.0); see final_run()
    "marker":        6.0,    # flame / snowflake marker box
}

# Column widths of the major venues, in points, at 100% \linewidth.
VENUE_WIDTH = {
    "cvpr":    {"single": 237.1, "double": 496.8},
    "iccv":    {"single": 237.1, "double": 496.8},
    "eccv":    {"single": 347.1, "double": 347.1},   # single-column LNCS
    "neurips": {"single": 397.5, "double": 397.5},
    "iclr":    {"single": 397.5, "double": 397.5},
    "icml":    {"single": 234.9, "double": 487.8},
    "aaai":    {"single": 238.5, "double": 504.0},
    "acl":     {"single": 219.1, "double": 455.2},
    "icra":    {"single": 252.0, "double": 516.0},   # ieeeconf
    "iros":    {"single": 252.0, "double": 516.0},
    "siggraph":{"single": 244.0, "double": 516.0},
    "generic": {"single": 240.0, "double": 500.0},
}

# Aspect ratios the corpus actually ships, width/height of the artwork.
# Median 1.82; 10th/90th percentile 0.96 and 3.35.  A double-column framework
# figure clusters near 3.0, a single-column module detail near 1.2.
ASPECT = {"teaser": 1.6, "framework": 2.6, "module": 1.3, "panel": 1.8}


# --------------------------------------------------------------------------
# Arrowheads.  The SVG marker and the PPTX line end have to agree, and the
# three editors a user is likely to have disagree with each other.  Measured
# by exporting the same PPTX (every w/len preset at line widths 0.3-2 pt):
#
#   LibreOffice 26   head = k x max(lw, 1.984 pt), k = 2 / 3 / 5 (sm/med/lg)
#   Keynote 14       head = k x lw,  k = 2.62 / 4.31 / 7.75
#   WPS (macOS)      head = k x lw,  k = 2 / 3 / 5   (reported by a user)
#
# PowerPoint is not available to measure; LibreOffice's 1.984 pt clamp is its
# emulation of PowerPoint's and is taken as PowerPoint's behaviour.  No single
# preset is right everywhere for a 0.6 pt line, so the writer is told which
# application will produce the paper PDF (``figure: {pptx_app: ...}``) and
# picks the preset for that one; the SVG then draws exactly that head.  With
# ``pptx_heads: shape`` the heads are drawn as triangles instead and look the
# same in every application, at the cost of not following a moved connector.
# --------------------------------------------------------------------------
ARROW_APPS = {
    "office":   {"floor": 1.984, "k": {"sm": 2.0, "med": 3.0, "lg": 5.0}},
    "wps":      {"floor": 0.0,   "k": {"sm": 2.0, "med": 3.0, "lg": 5.0}},
    "keynote":  {"floor": 0.0,   "k": {"sm": 2.62, "med": 4.31, "lg": 7.75}},
}
ARROW_APPS["libreoffice"] = ARROW_APPS["office"]
ARROW_APPS["powerpoint"] = ARROW_APPS["office"]
DEFAULT_ARROW_APP = "office"
ARROW_PRESET = ("sm", "sm")          # (width, length) of the default head


def head_size(lw, preset=ARROW_PRESET, app=DEFAULT_ARROW_APP):
    """(length, width) in points that *app* draws for a ``preset`` line end."""
    m = ARROW_APPS.get(app, ARROW_APPS["office"])
    base = max(float(lw), m["floor"])
    w, ln = preset
    return m["k"][ln] * base, m["k"][w] * base


def pick_preset(lw, length=None, width=None, app=DEFAULT_ARROW_APP):
    """The (w, len) preset whose drawn head is closest to the wanted size."""
    length = GEOM["arrow_len"] if length is None else length
    width = GEOM["arrow_wid"] if width is None else width
    m = ARROW_APPS.get(app, ARROW_APPS["office"])
    base = max(float(lw), m["floor"]) or 1e-6
    best = lambda want: min(m["k"], key=lambda p: abs(m["k"][p] * base - want))
    return best(width), best(length)


def arrow_geometry(lw, app=DEFAULT_ARROW_APP):
    """Head (length, width) the SVG must draw so it matches the PPTX."""
    return head_size(lw, pick_preset(lw, app=app), app)


def final_run(lw, app=DEFAULT_ARROW_APP):
    """Shortest last segment a bent arrow may have.

    WPS (and, by report, PowerPoint) aims a line-end arrowhead by stepping
    back one head length along the path.  When the last segment is shorter
    than that, the step lands on the previous segment and the head is drawn
    at 45 degrees.  2.5 head lengths, and never under 8 pt, leaves room for
    the head plus a visible run of shaft.  A *straight* arrow is immune: there
    is no earlier segment to land on.
    """
    return max(2.5 * worst_head(lw, app), 8.0)


def worst_head(lw, app=DEFAULT_ARROW_APP):
    """Longest head any of the three editors draws for the preset *app* gets."""
    preset = pick_preset(lw, app=app)
    return max(head_size(lw, preset, a)[0] for a in ("office", "wps", "keynote"))


def stroke_width(value, default=None):
    """Accept a number or one of the named weights (hairline/box/flow/...)."""
    if value is None:
        return default
    if isinstance(value, str):
        if value in STROKE:
            return STROKE[value]
        try:
            return float(value)
        except ValueError:
            return default
    return float(value)


def _fam_tint(role):
    return ROLES.get(role, ROLES["neutral"])


def fill_for(role, tint=None, system=None):
    fam, default = _fam_tint(role)
    return palettes.pick(fam, tint or default, system or SYSTEM)


def stroke_for(role, tint="strong", system=None):
    """Outline colour matched to the fill family."""
    fam, _ = _fam_tint(role)
    sys_ = system or SYSTEM
    if sys_ == "drawio":
        return palettes.outline(fam, "drawio")
    return palettes.pick(fam, tint, sys_)


def accent_for(role, system=None):
    """Saturated colour for arrows and text that must read as 'this entity'."""
    fam, _ = _fam_tint(role)
    return palettes.pick(fam, "deep", system or SYSTEM)


def resolve_color(value, default=None, ink=False, system=None):
    """Accept '#rrggbb', 'family.tint', 'family', a role name or a signal name.

    ``ink=True`` is used for strokes, arrows and coloured label text: a bare
    role or family name then resolves to the saturated end of that family
    instead of the pale fill, which is what published figures do when they
    colour a data path after the module it comes from.
    """
    if value is None:
        return default
    v = str(value).strip()
    if v.startswith("#"):
        return v.upper()
    if v in ("none", "None", "transparent"):
        return "none"
    sys_ = system or SYSTEM
    if "." in v:
        fam, tint = v.split(".", 1)
        if fam in FAMILIES and tint in TINT_INDEX:
            return FAMILIES[fam][TINT_INDEX[tint]]
        if fam in ("office", "drawio") and tint in FAMILIES:
            return palettes.pick(tint, "light", fam)
    if v in FAMILIES:
        return FAMILIES[v][TINT_INDEX["deep" if ink else "light"]]
    if v in ROLES:
        return accent_for(v, sys_) if ink else fill_for(v, None, sys_)
    if v in SIGNAL:
        return SIGNAL[v]
    named = {"ink": INK, "muted": MUTED_INK, "frame": FRAME_GREY,
             "softframe": SOFT_FRAME, "paper": PAPER,
             "white": "#FFFFFF", "black": "#000000", "darkred": "#B1001C"}
    if v in named:
        return named[v]
    return v
