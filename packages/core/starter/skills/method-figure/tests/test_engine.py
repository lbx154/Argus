#!/usr/bin/env python3
"""Self-contained regression tests.  Run: python3 tests/test_engine.py"""

import os
import sys
import zipfile
import xml.dom.minidom as minidom

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from cvprfig import Figure, load_path, miniyaml, style, text as T   # noqa: E402
from cvprfig import edges as edgemod                                 # noqa: E402
from cvprfig import layout as L                                      # noqa: E402
from cvprfig import vsdx as vsdx_writer, pptx as pptx_writer         # noqa: E402

FAILED = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILED.append(msg)


def section(name):
    print("\n== %s" % name)


# ---------------------------------------------------------------- metrics
section("text metrics")
w, h, lines = T.measure("Temporal-Spatial MHSA", 7.0)
check(abs(w - 70.97) < 0.1, "measured width matches the published box (71.0 pt)")
check(len(lines) == 1, "single line")
check(T.measure("a\nb", 7.0)[1] > 7.0, "multiline is taller than one line")
runs = T.parse_runs("Q^{t} **b** *i*")
check(any(r[3] > 0 for r in runs), "superscript parsed")
check(any(r[1] for r in runs), "bold parsed")
check(any(r[2] for r in runs), "italic parsed")
check(T.glyph_width("×", "times") == 564, "multiplication sign has a real width")

# ----------------------------------------------------------------- yaml
section("yaml fallback parser")
doc = """
figure: {id: t, venue: cvpr}
panels:
  - id: p
    title: "A"
    body:
      - {id: a, text: X}
      - row: [{id: b, text: Y}]
edges:
  - from: a
    to: b
"""
real = miniyaml._pyyaml
try:
    miniyaml._pyyaml = None
    d = miniyaml.load(doc)
finally:
    miniyaml._pyyaml = real
check(d["figure"]["id"] == "t", "nested inline mapping")
check(d["panels"][0]["body"][1]["row"][0]["id"] == "b", "nested flow sequence")
check(d["edges"][0]["from"] == "a", "block mapping inside a sequence")
if real is not None:
    for _case in ('a: "Rocket \\U0001F680"', 'a: "\\u03bc x \\u00d7"',
                  'a: "line\\nbreak"', 'a: "tab\\there"', "a: 'it''s'",
                  'a: "quote \\" end"'):
        _py = real.safe_load(_case)["a"]
        miniyaml._pyyaml = None
        try:
            _mini = miniyaml.load(_case)["a"]
        finally:
            miniyaml._pyyaml = real
        check(_py == _mini, "escape parity for %s" % _case)
    y = miniyaml.load("a: off\nb: no\nc: true\nd: false\n")
    check(y["a"] == "off" and y["b"] == "no", "on/off/yes/no stay strings, so they work as ids")
    check(y["c"] is True and y["d"] is False, "true/false stay boolean")

section("parser equivalence")
if real is not None:
    for _name in ("pipeline-4stage", "teaser-comparison", "attention-tokens",
                  "streaming-worldmodel", "module-detail", "dual-branch",
                  "gated-module", "blindspot-teaser", "study-overview",
                  "factorial-2x2", "taxonomy",
             # v2, from the corpus scan
             "framework-with-io", "surroundview-pipeline", "trainable-frozen",
             "teacher-student", "wrapped-pipeline"):
        _p = os.path.join(ROOT, "templates", _name + ".yaml")
        _src = open(_p, encoding="utf-8").read()
        _a = Figure(miniyaml.load(_src), base=os.path.join(ROOT, "templates")).tostring()
        miniyaml._pyyaml = None
        try:
            _b = Figure(miniyaml.load(_src), base=os.path.join(ROOT, "templates")).tostring()
        finally:
            miniyaml._pyyaml = real
        check(_a == _b, "%s renders identically with and without PyYAML" % _name)

# ---------------------------------------------------------------- layout
section("layout")
spec = {
    "figure": {"id": "t", "venue": "cvpr", "width": "double"},
    "panels": [{"id": "p", "frame": "dashed", "body": [
        {"id": "short", "text": "A"},
        {"id": "long", "text": "A much longer module label"},
    ]}],
}
fig = Figure(spec)
check(abs(fig.nodes["short"].w - fig.nodes["long"].w) < 0.01,
      "siblings in a column snap to a common width")
check(fig.nodes["short"].cx == fig.nodes["long"].cx, "and stay centred on one axis")

spec2 = {"figure": {"id": "t"}, "panels": [{"id": "p", "row": [
    {"id": "plain", "shape": "sharpbox", "w": 20, "h": 15},
    {"id": "capped", "shape": "sharpbox", "w": 20, "h": 15, "caption": "T"},
]}]}
f2 = Figure(spec2)
box_plain = f2.nodes["plain"].h - f2.nodes["plain"].cap
box_capped = f2.nodes["capped"].h - f2.nodes["capped"].cap
check(abs(box_plain - box_capped) < 0.01,
      "a caption does not change the box height under uniform snapping")

# --------------------------------------------------------------- routing
section("edge routing")
import types
a = types.SimpleNamespace(port=lambda s: (100.0, 50.0))
b = types.SimpleNamespace(port=lambda s: (40.0, 90.0))
pts = edgemod._dedup(edgemod._path_points(a, "s", b, "n", {}))
check(abs(pts[0][0] - pts[1][0]) < 0.01, "leaves a south port travelling straight down")
check(abs(pts[-1][0] - pts[-2][0]) < 0.01, "arrives at a north port travelling straight down")
pts = edgemod._dedup(edgemod._path_points(a, "e", b, "w", {}))
check(abs(pts[0][1] - pts[1][1]) < 0.01, "leaves an east port travelling horizontally")
straight = edgemod._dedup(edgemod._path_points(
    types.SimpleNamespace(port=lambda s: (0.0, 0.0)),
    "e",
    types.SimpleNamespace(port=lambda s: (50.0, 0.0)), "w", {}))
check(len(straight) == 2, "aligned facing ports give one straight run")

# ---------------------------------------------------------------- colour
section("colour resolution")
check(style.resolve_color("core") == "#FBE5D6", "role -> pale fill (core is orange in v3)")
check(style.resolve_color("core", ink=True) == "#C55A11", "role -> saturated ink")
check(style.resolve_color("gold.soft") == "#FFE699", "family.tint")
check(style.resolve_color("#abcdef") == "#ABCDEF", "hex passes through, normalised")

# -------------------------------------------------------------- templates
section("templates render and export")
TEMPLATES = ("pipeline-4stage", "teaser-comparison", "attention-tokens",
             "streaming-worldmodel", "module-detail", "dual-branch",
             "gated-module", "blindspot-teaser", "study-overview",
             "factorial-2x2", "taxonomy",
             # v2, from the corpus scan
             "framework-with-io", "surroundview-pipeline", "trainable-frozen",
             "teacher-student", "wrapped-pipeline")

for name in TEMPLATES:
    path = os.path.join(ROOT, "templates", name + ".yaml")
    fig = Figure(load_path(path), base=os.path.join(ROOT, "templates"))
    svg = fig.tostring()
    check(svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), "%s renders SVG" % name)
    minidom.parseString(svg)
    check(fig.scale >= 0.85, "%s fits the column within 15%% (scale %.2f)" % (name, fig.scale))
    eff = style.TYPE["node"]["size"] * fig.scale
    check(eff >= style.MIN_RENDERED_PT, "%s type stays legible (%.1f pt)" % (name, eff))

out = os.path.join(HERE, "_out")
os.makedirs(out, exist_ok=True)

section("new shapes")
_shapes = {"figure": {"id": "sh", "venue": "cvpr", "width": "single"},
           "panels": [{"id": "p", "body": [
               {"id": "fs", "shape": "framestrip", "pattern": "..####..##", "cell": 5},
               {"id": "dg", "shape": "dist", "kind": "gauss", "w": 40, "h": 20},
               {"id": "dl", "shape": "dist", "kind": "laplace", "w": 40, "h": 20},
               {"id": "db", "shape": "dist", "kind": "bimodal", "w": 40, "h": 20},
               {"id": "br", "shape": "brace", "h": 24}]}]}
_f = Figure(_shapes)
_svg = _f.tostring()
minidom.parseString(_svg)
check(abs(_f.nodes["fs"].w - (10 * 5 + 9 * 1.2)) < 0.01,
      "framestrip width follows its pattern length")
check("<path" in _svg, "density glyph emits a path")
for _w, _ext in ((vsdx_writer, "vsdx"), (pptx_writer, "pptx")):
    _p = os.path.join(out, "shapes." + _ext)
    _w.write(_f, _p)
    _z = zipfile.ZipFile(_p)
    for _n in _z.namelist():
        if _n.endswith((".xml", ".rels")):
            minidom.parseString(_z.read(_n))
    check(True, "new shapes survive the %s writer" % _ext)

section("named stroke weights")
check(style.stroke_width("emphasis") == style.STROKE["emphasis"], "name resolves")
check(style.stroke_width(1.7) == 1.7, "number passes through")
check(style.stroke_width(None, 0.65) == 0.65, "default applies")
_lw = Figure({"figure": {"id": "lw"}, "panels": [{"id": "p", "body": [
    {"id": "a", "text": "x", "lw": "emphasis"}]}]})
check("stroke-width='0.8'" in _lw.tostring(), "a named weight reaches the SVG")

section("container body forms")
_forms = Figure({"figure": {"id": "cf"}, "panels": [{"id": "p", "body": [
    {"group": {"id": "g", "frame": "region",
               "row": {"gap": 5, "body": [
                   {"group": {"id": "inner", "body": [{"id": "leaf", "text": "L"}]}},
                   {"id": "sib", "text": "S"}]}}}]}]})
_forms.tostring()
check("inner" in _forms.nodes and "leaf" in _forms.nodes,
      "a group nested inside a row-with-options is addressable")

# ------------------------------------------------------------- containers
section("vsdx and pptx packaging")
fig = Figure(load_path(os.path.join(ROOT, "templates", "pipeline-4stage.yaml")),
             base=os.path.join(ROOT, "templates"))

vp = os.path.join(out, "t.vsdx")
info = vsdx_writer.write(fig, vp)
z = zipfile.ZipFile(vp)
for n in z.namelist():
    minidom.parseString(z.read(n))
check("visio/pages/page1.xml" in z.namelist(), "vsdx has a page part")
check("[Content_Types].xml" in z.namelist(), "vsdx has content types")
check(z.read("visio/pages/page1.xml").decode().count("<Shape ") > 100,
      "vsdx carries native shapes (%d)" % z.read("visio/pages/page1.xml").decode().count("<Shape "))
check(info["shapes"] > 100, "writer reports the shape count")

pp = os.path.join(out, "t.pptx")
info = pptx_writer.write(fig, pp)
z = zipfile.ZipFile(pp)
for n in z.namelist():
    if n.endswith(".xml") or n.endswith(".rels"):
        minidom.parseString(z.read(n))
check("ppt/slides/slide1.xml" in z.namelist(), "pptx has a slide")
check("ppt/theme/theme1.xml" in z.namelist(), "pptx has a theme")
slide = z.read("ppt/slides/slide1.xml").decode()
check(slide.count("<p:sp>") > 100, "pptx carries native shapes (%d)" % slide.count("<p:sp>"))

# --------------------------------------------------------------- auditor
section("auditor")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import validate as V   # noqa: E402

bad = os.path.join(out, "bad.yaml")
with open(bad, "w") as fh:
    fh.write("""
figure: {id: bad, venue: cvpr, width: double}
panels:
  - id: p
    body:
      - row: [{id: a, text: "Encoder", fill: "#FF00FF", size: 4.0},
              {id: b, text: "Encoder", fill: "#FFD965"},
              {id: c, text: "Rocket \\U0001F680"}]
edges: [{from: a, to: b}]
""")
_, rep = V.audit(bad)
codes = set(r["code"] for r in rep.rows)
check("text-too-small" in codes, "catches sub-5 pt type")
check("inconsistent-role" in codes, "catches one concept drawn in two colours")
check("off-palette" in codes, "catches off-palette fills")
check("emoji" in codes, "catches emoji")
check(rep.counts()["error"] > 0, "reports errors on a bad figure")

for name in TEMPLATES:
    _, rep = V.audit(os.path.join(ROOT, "templates", name + ".yaml"))
    check(rep.counts()["error"] == 0, "%s passes the auditor with no errors" % name)



# ============================================================ v2: shapes
section("v2 shape vocabulary")
_v2 = Figure(miniyaml.load("""
figure: {id: v2, venue: cvpr, width: double}
layout: row
panels:
  - id: p
    body:
      - row:
          gap: 8
          align: center
          body:
            - {id: cam, shape: cameraring, cell: 24}
            - {id: vox, shape: voxelgrid, grid: 3, side: 28, role: core}
            - {id: fm, shape: planestack, n: 4, w: 20, h: 26, role: attention}
            - {id: gs, shape: gaussians, n: 6, w: 46, h: 28, seed: 3}
            - {id: mt, shape: marker, kind: trainable, d: 8}
            - {id: mf, shape: marker, kind: frozen, d: 8}
            - {id: ig, shape: imagegrid, rows: 2, cols: 3, cell: 20}
            - {id: ln, shape: lane, text: "flow", w: 54, h: 16}
"""))
_svg = _v2.tostring()
check("<ellipse" in _svg, "gaussians emit ellipses")
check(_svg.count("<path") >= 2, "markers emit vector paths, not emoji")
for _sid in ("cam", "vox", "fm", "gs", "mt", "mf", "ig", "ln"):
    check(_sid in {i.id for i in L.walk(_v2.items)}, "%s is laid out" % _sid)
_, _rep = V.audit({"figure": {"id": "v2", "venue": "cvpr", "width": "double"},
                   "panels": [{"id": "p", "body": [{"id": "m", "shape": "marker",
                                                    "kind": "trainable", "d": 8}]}]})
check(_rep.counts()["error"] == 0, "a vector marker does not trip the emoji check")

section("v2 markup escaping")
check(T.parse_runs(r"kernel\_size")[0][0] == "kernel_size", "backslash escapes _")
check(T.parse_runs(r"a\*b")[0][0] == "a*b", "backslash escapes *")
check(T.parse_runs("x_i")[1][3] == -1, "unescaped _ still subscripts")

section("v2 image sizing")
import tempfile as _tf, struct as _st, zlib as _zl
def _png(path, w, h):
    def chunk(t, d):
        return (_st.pack(">I", len(d)) + t + d
                + _st.pack(">I", _zl.crc32(t + d) & 0xFFFFFFFF))
    ihdr = _st.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                           + chunk(b"IDAT", _zl.compress(raw)) + chunk(b"IEND", b""))
_tmp = _tf.mkdtemp()
_p = os.path.join(_tmp, "wide.png")
_png(_p, 64, 16)
from cvprfig import imgsize as _IS
check(_IS.size(_p) == (64, 16), "png header is read")
check(abs(_IS.aspect(_p) - 4.0) < 1e-6, "aspect comes from the file")
_fig = Figure(miniyaml.load("""
figure: {id: im, venue: cvpr, width: double}
layout: row
panels: [{id: p, body: [{id: pic, shape: image, w: 80, src: wide.png}]}]
"""), base=_tmp)
_pic = {i.id: i for i in L.walk(_fig.items)}["pic"]
check(abs(_pic.h - 20.0) < 0.5, "height follows the real aspect (80/4 = 20)")
check("data:image/png;base64" in _fig.tostring(), "the raster is embedded, not linked")

# ============================================================ v2: code2fig
section("v2 code to figure")
from cvprfig.code2fig import torchscan as _TS, mmconfig as _MC, emit as _EM
_src = os.path.join(_tmp, "m.py")
_MODEL_SRC = "\n".join([
    "import torch.nn as nn",
    "class Net(nn.Module):",
    "    def __init__(self):",
    "        self.backbone = nn.Conv2d(3, 64)",
    "        self.neck = nn.Sequential(nn.Conv2d(64, 128), nn.ReLU())",
    "        self.ctx_head = nn.Linear(128, 10)",
    "        self.depth_head = nn.Linear(128, 1)",
    "        self.dropout = nn.Dropout(0.1)",
    "        self.loss = nn.CrossEntropyLoss()",
    "    def forward(self, x):",
    "        f = self.backbone(x)",
    "        f = self.neck(f)",
    "        a = self.ctx_head(f)",
    "        b = self.depth_head(f)",
    "        return a, b",
])
open(_src, "w").write(_MODEL_SRC)
_g, _name, _all = _TS.build([_src])
_ids = {n.id for n in _g.nodes}
check(_name == "Net", "picks the model class")
check("backbone" in _ids and "neck" in _ids, "submodules become nodes")
check("ctx_head" in _ids and "depth_head" in _ids, "both branches survive")
check("dropout" not in _ids, "plumbing modules are pruned")
check("loss" not in _ids, "loss is dropped unless asked for")
check(_g.get("backbone").role == "backbone", "role inferred from the name")
check(("backbone", "neck", None) in _g.edges, "dataflow edge recovered from forward")
check(len(_g.topo_layers()) >= 3, "the graph layers left to right")

_cfg = os.path.join(_tmp, "cfg.py")
_CFG_SRC = "\n".join([
    "model = dict(",
    "    type='Detector',",
    "    img_backbone=dict(type='ResNet', depth=50, out_channels=256),",
    "    img_neck=dict(type='FPN', in_channels=256, out_channels=128),",
    "    bbox_head=dict(type='Head', num_classes=10),",
    "    train_cfg=dict(lr=0.1))",
])
open(_cfg, "w").write(_CFG_SRC)
_g2, _n2 = _MC.build(_cfg)
_ids2 = {n.id for n in _g2.nodes}
check(_n2 == "Detector", "config type is the title")
check({"img_backbone", "img_neck", "bbox_head"} <= _ids2, "config stages become nodes")
check("train_cfg" not in _ids2, "train_cfg is skipped")
check("depth 50" in (_g2.get("img_backbone").note or ""), "channel counts are captured")

_spec, _note = _EM.fit(_g2, venue="cvpr", width="double")
check(Figure(_spec).fit_scale >= 0.94, "the emitted draft fits its column")
_, _r2 = V.audit(_spec)
check(_r2.counts()["error"] == 0, "the emitted draft passes the auditor")

section("v2 recalibrated constants")
check(style.MIN_RENDERED_PT == 6.0, "type floor is 6.0 pt (v3)")
check(style.STROKE["box"] == 0.36, "box stroke is the corpus mode")
check(style.FAMILIES["blue"][1] == "#DEEBF7", "blue.light is Office Accent 5 Lighter 80%")
from cvprfig import palettes as _P
check(_P.DRAWIO["green"] == ("#D5E8D4", "#82B366"), "draw.io pairs are kept whole")
check(len(_P.TAB10) == 10, "tab10 is available for plot panels")


# ======================================================================== v3
import tempfile as _tf, re as _re, struct as _st, zlib as _zl
from cvprfig import ooxml as _OX
import audit_pptx as _AP

section("v3 type and maths")
check(style.TYPE["node"]["size"] == 7.5 and style.WARN_RENDERED_PT == 6.5,
      "body type 7.5 pt, warning tier 6.5 pt")
_runs = T.parse_runs("$z_{1:4}$")
check(_runs[0] == ("z", False, True, 0) and _runs[1][3] == -1 and not _runs[1][2],
      "maths: variable italic, index upright and subscripted")
check(T.plain("$N \\times C$") == "N × C", "binary operators are spaced like TeX")
check(T.plain("$T-2$") == "T − 2" and T.plain("$-x$") == "−x", "minus: binary vs unary")
check(T.plain("$\\times L$") == "×L", "an operator with no left operand is not spaced")
check(T.plain("$\\hat z_{5:10}$")[0] == "ẑ", "accents compose (z-hat)")
check(T.plain("cost \\$5") == "cost $5", "\\$ is a literal dollar")
_sy = [x["key"] for x in T.symbols("$\\tilde c_s = c_s + W[d_1;\\dots;d_4]$")]
check(_sy[:4] == ["c̃", "c", "W", "d"] and "[;]" in _sy, "symbol extraction keeps accents apart")
check(T.symbols("BEV features z_{1:4}")[0]["upright"], "old upright markup is flagged")
check([x["key"] for x in T.symbols("×N")] == ["N"], "a repeat badge names a symbol")
with style.scope(1.2):
    check(abs(style.TYPE["node"]["size"] - 9.0) < 1e-6, "type_scale scales the table")
check(style.TYPE["node"]["size"] == 7.5, "...and restores it")

section("v3 arrows")
check(style.pick_preset(0.6) == ("sm", "sm"), "thin lines get the sm/sm head")
check(abs(style.head_size(0.6, ("sm", "sm"), "office")[0] - 3.968) < 0.01,
      "LibreOffice/PowerPoint clamp the head base at 1.984 pt")
check(abs(style.head_size(0.6, ("sm", "sm"), "keynote")[0] - 1.572) < 0.01, "Keynote scales with lw")
check(style.final_run(0.6) >= 2.5 * 3.96, "final run is 2.5 heads")


def _fig(spec):
    return Figure(spec)


_g = _fig({"figure": {"width": 240}, "layout": "row", "gap": 12,
           "panels": [{"id": "p", "body": [{"id": "a", "text": "A"}]},
                      {"id": "q", "body": [{"id": "b", "text": "B", "dy": 30}]}],
           "edges": [{"from": "a", "to": "b"}]})
_r = _g.routes[0]
check(len(_r.pts) == 4 and abs(_r.pts[-1][0] - _r.pts[-2][0]) >= style.final_run(0.6) - 0.01,
      "a dog-leg keeps its final run")
_g = _fig({"figure": {"width": 240}, "layout": "row", "gap": 12,
           "panels": [{"id": "p", "body": [{"id": "a", "text": "A"}]},
                      {"id": "q", "body": [{"id": "b", "text": "B", "dy": 2.5}]}],
           "edges": [{"from": "a", "to": "b"}]})
check(len(_g.routes[0].pts) == 2, "a near miss becomes one straight arrow, not a 2 pt jog")
_g = _fig({"figure": {"width": 300}, "layout": "row", "gap": 4, "fit": "tight",
           "panels": [{"id": "p", "body": [{"id": "a1", "text": "A"}, {"id": "a2", "text": "B"}]},
                      {"id": "q", "body": [{"id": "b1", "text": "C", "dy": 30},
                                           {"id": "b2", "text": "D", "dy": 30}]}],
           "edges": [{"from": "a1.e", "to": "b1.w"}, {"from": "a2.e", "to": "b2.w"}]})
_xs = sorted(set(round(r.pts[1][0], 1) for r in _g.routes))
check(len(_xs) == 2 and abs(_xs[1] - _xs[0]) >= style.GEOM["lane"] - 0.01,
      "two dog-legs in one corridor get separate lanes")
check(_g.gaps[0] >= _g.corridor_demand()[0] - 0.01, "the corridor is widened to fit them")
for _rot in range(4):
    for _pts in ([(0, 0), (30, 0)], [(0, 0), (0, 20)], [(0, 0), (20, 0), (20, 15)],
                 [(0, 0), (10, 0), (10, 15), (30, 15)], [(0, 0), (0, 10), (-20, 10), (-20, 25)],
                 [(5, 5), (-10, 5), (-10, -20), (-30, -20)]):
        _P = [(x, y) if _rot == 0 else ((-y, x) if _rot == 1 else ((-x, -y) if _rot == 2 else (y, -x)))
              for x, y in _pts]
        _geo = pptx_writer.connector_geometry(_P)
        if _geo is None:
            check(False, "connector geometry for %s" % (_P,))
            continue
        prst, off, ext, rot, fh, fv, adj = _geo
        _xf = {"x": off[0], "y": off[1], "w": ext[0], "h": ext[1], "rot": rot / 60000.0,
               "fh": fh, "fv": fv}
        _back = [_OX._local(_xf)(p) for p in _OX._preset_path(prst, ext[0], ext[1],
                                                                {"adj1": adj or 50000})]
        if not all(abs(a[0] - b[0]) < 0.05 and abs(a[1] - b[1]) < 0.05 for a, b in zip(_back, _P)):
            check(False, "connector round trip %s -> %s" % (_P, _back))
check(True, "connector geometry round-trips 24 routes through the PPTX reader")

section("v3 captions, ports and alignment")
_g = _fig({"figure": {"width": 240}, "layout": "row",
           "panels": [{"id": "p", "row": [{"id": "img", "shape": "image", "w": 30, "h": 20,
                                            "caption": "a much wider caption here"},
                                           {"id": "m", "text": "Module"}]}],
           "edges": [{"from": "img", "to": "m"}]})
_im = _g.nodes["img"]
check(abs(_im.body()[2] - 30) < 0.01, "a wide caption no longer stretches its image")
check(abs(_g.routes[0].pts[0][0] - (_im.body()[0] + 30)) < 0.01, "arrows leave the shape, not the caption")
check(len(_g.routes[0].pts) == 2, "captioned shape and its neighbour share a centre line")
check(_im.port("s")[1] > _im.body()[1] + _im.body()[3] + 3, "the south port sits below the caption")
_g = _fig({"figure": {"width": 240}, "layout": "col",
           "panels": [{"id": "p", "row": [{"id": "a", "text": "A"}, {"id": "b", "text": "Bee"}]},
                      {"id": "q", "row": [{"id": "c", "text": "C", "align_x": "b"}]}]})
check(abs(_g.nodes["c"].cx - _g.nodes["b"].cx) < 0.01, "align_x puts one node under another")
_g = _fig({"figure": {"width": 240}, "layout": "row",
           "panels": [{"id": "p", "title": "A rather long stage title", "body": [{"id": "a", "text": "A"}]}]})
_tw = T.measure("A rather long stage title", style.TYPE["stage_title"]["size"], "times", True, True)[0]
check(_g.items[0].w >= _tw, "a stage is never narrower than its title")
_d = _tf.mkdtemp()
open(os.path.join(_d, "paper.yaml"), "w").write(
    "venue: iccv\nroles: {core: green.light}\nsymbols:\n  W: {meaning: projection, defined_in: caption}\n")
_g = Figure({"paper": "paper.yaml", "body": [{"id": "a", "text": "$W$", "role": "core"}]}, base=_d)
_svg = _g.tostring()
check("#E2F0D9" in _svg and (_g.spec.get("symbols") or {}).get("W"), "paper.yaml supplies roles and symbols")
check(style.resolve_color("core") == "#FBE5D6", "...without leaking into the next figure")

section("v3 auditor")


def _codes(spec, base=None):
    _f, _rep = V.audit(spec, base=base)
    return [r["code"] for r in _rep.rows if r["level"] in ("error", "warning")]


_base = {"figure": {"width": 240}, "layout": "row",
         "panels": [{"id": "p", "row": [{"id": "a", "text": "$x_t$"}, {"id": "op", "shape": "op", "op": "+"},
                                          {"id": "b", "text": "Out"}]}],
         "edges": [{"from": "a", "to": "op"}, {"from": "op", "to": "b"}]}
_c = _codes(dict(_base))
check(_c.count("undefined-symbol") == 2, "undefined symbols are errors (x and the oplus)")
_c = _codes(dict(_base, symbols={"x_t": {"meaning": "input", "defined_in": "caption"},
                                 "\\oplus": {"meaning": "sum", "defined_in": "caption"},
                                 "Q": {"meaning": "unused", "defined_in": "caption"}},
                 caption="Input $x_t$ is summed ($\\oplus$)."))
check("undefined-symbol" not in _c and "symbol-not-in-caption" not in _c, "caption definitions are found")
check("unused-symbol" in _c, "a registered symbol the figure never shows is reported")
_c = _codes(dict(_base, symbols={"x_t": {"meaning": "input", "defined_in": "caption"},
                                 "\\oplus": {"meaning": "sum", "defined_in": "caption"}},
                 caption="Input is summed."))
check(_c.count("symbol-not-in-caption") == 2, "a caption that never writes the symbol fails")
_c = _codes(dict(_base, legend={"items": [{"text": "ghost", "color": "red", "kind": "dashed"}]}))
check("legend-unused-entry" in _c, "a legend entry nothing uses is an error")
_c = _codes({"figure": {"width": 240}, "body": [{"id": "a", "text": "A", "fill": "#3B82F6"},
                                                {"id": "b", "text": "B"}],
             "edges": [{"from": "a", "to": "b"}]})
check("ai-palette" in _c, "Tailwind blue-500 is flagged")
_c = _codes({"figure": {"width": 240}, "body": [{"id": "a", "text": "A", "size": 5.5}]})
check("text-too-small" in _c, "5.5 pt fails the 6.0 pt floor")
_png = os.path.join(_d, "tiny.png")
_raw = b"".join(b"\x00" + b"\xff\xff\xff" * 40 for _ in range(30))
_chunk = lambda t, d: _st.pack(">I", len(d)) + t + d + _st.pack(">I", _zl.crc32(t + d) & 0xffffffff)
open(_png, "wb").write(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", _st.pack(">IIBBBBB", 40, 30, 8, 2, 0, 0, 0))
                       + _chunk(b"IDAT", _zl.compress(_raw)) + _chunk(b"IEND", b""))
_c = _codes({"figure": {"width": 240}, "body": [{"id": "im", "shape": "image", "src": "tiny.png", "w": 60}]},
            base=_d)
check("image-low-ppi" in _c, "a 40 px image printed 60 pt wide (48 ppi) fails")

section("v3 PPTX writer and auditor")
_spec = load_path(os.path.join(ROOT, "templates", "framework-with-io.yaml"))
_f = Figure(_spec, base=os.path.join(ROOT, "templates"))
_pp = os.path.join(_d, "f.pptx")
_info = pptx_writer.write(_f, _pp)
_xml = zipfile.ZipFile(_pp).read("ppt/slides/slide1.xml").decode()
check(_info["connectors_glued"] >= 8 and "<a:stCxn" in _xml, "arrows are glued connectors")
check('w="sm" len="sm"' in _xml, "heads use the preset that matches the SVG")
check("<p:bg>" in _xml, "the slide has an explicit background (Keynote)")
minidom.parseString(_xml)
check(True, "slide XML is well formed")
_rep, _meta = _AP.audit(_pp, ref=os.path.join(ROOT, "templates", "framework-with-io.yaml"))
check(_rep.counts()["error"] == 0, "the engine's own PPTX passes audit_pptx")
_flat = _re.sub(r' baseline="-?\d+"', "", _xml)
_zin = zipfile.ZipFile(_pp)
_pp2 = os.path.join(_d, "flat.pptx")
_zout = zipfile.ZipFile(_pp2, "w", zipfile.ZIP_DEFLATED)
for _it in _zin.infolist():
    _zout.writestr(_it, _flat if _it.filename == "ppt/slides/slide1.xml" else _zin.read(_it.filename))
_zout.close()
_spec2 = {"figure": {"width": 240}, "body": [{"id": "a", "text": "$z_{1:4}$ and $O_{5:10}$"}],
          "symbols": {"z": {"meaning": "m", "defined_in": "figure"}, "O": {"meaning": "m", "defined_in": "figure"}}}
_f2 = Figure(_spec2)
_p3 = os.path.join(_d, "s.pptx")
pptx_writer.write(_f2, _p3)
_x3 = zipfile.ZipFile(_p3).read("ppt/slides/slide1.xml").decode()
_z3 = zipfile.ZipFile(_p3)
_p4 = os.path.join(_d, "s_flat.pptx")
_zo = zipfile.ZipFile(_p4, "w", zipfile.ZIP_DEFLATED)
for _it in _z3.infolist():
    _zo.writestr(_it, _re.sub(r' baseline="-?\d+"', "", _x3) if _it.filename == "ppt/slides/slide1.xml"
                 else _z3.read(_it.filename))
_zo.close()
_rep4, _ = _AP.audit(_p4)
check(any(r["code"] == "lost-subscript" for r in _rep4.rows), "flattened subscripts are caught")
_spec5 = {"figure": {"width": 240}, "body": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
          "edges": [{"from": "a", "to": "b", "dash": True}],
          "legend": {"items": [{"text": "flow", "kind": "dashed"}, {"text": "gone", "kind": "dashed",
                                                                     "color": "red"}]}}
_p5 = os.path.join(_d, "leg.pptx")
pptx_writer.write(Figure(_spec5), _p5)
_rep5, _ = _AP.audit(_p5)
check([r["code"] for r in _rep5.rows].count("legend-orphan") == 1, "an orphaned legend entry is caught in the PPTX")
check("<p:grpSp>" in zipfile.ZipFile(_p5).read("ppt/slides/slide1.xml").decode(), "the legend is a group")


# ====================================================================== v3.1
from cvprfig import imgcolor as _IC
import audit_pdf as _AD


def _repack(src, dst, edit):
    """Copy a PPTX, passing each part's text through ``edit(name, text)``."""
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for it in zi.infolist():
            b = zi.read(it.filename)
            if it.filename.endswith(".xml"):
                b = edit(it.filename, b.decode()).encode()
            zo.writestr(it, b)


def _rgb_png(path, w, h, px):
    raw = b"".join(b"\x00" + b"".join(bytes(px(x, y)) for x in range(w)) for y in range(h))
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", _st.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + _chunk(b"IDAT", _zl.compress(raw)) + _chunk(b"IEND", b""))


section("v3.1 theme shadows")
_SHDW = ('<a:effectStyle><a:effectLst><a:outerShdw blurRad="40000" dist="23000" dir="5400000">'
         '<a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw></a:effectLst>'
         '</a:effectStyle>')
_STYLE = ('<p:style><a:lnRef idx="0"><a:scrgbClr r="0" g="0" b="0"/></a:lnRef><a:fillRef idx="0">'
          '<a:scrgbClr r="0" g="0" b="0"/></a:fillRef><a:effectRef idx="2"><a:scrgbClr r="0" g="0" '
          'b="0"/></a:effectRef><a:fontRef idx="minor"/></p:style>')


def _shadowed(name, x):
    # FigGenie's pattern: an empty <a:effectLst/> on every shape, and a
    # <p:style> pointing at a theme effect style that draws a shadow.
    if name.startswith("ppt/theme/"):
        return _re.sub(r"<a:effectStyleLst>.*?</a:effectStyleLst>",
                       "<a:effectStyleLst>" + _SHDW * 3 + "</a:effectStyleLst>", x, flags=_re.S)
    if name == "ppt/slides/slide1.xml":
        return _re.sub(r"</a:prstGeom>(.*?)</p:spPr>", r"</a:prstGeom>\1<a:effectLst/></p:spPr>" + _STYLE,
                       x, flags=_re.S)
    return x


_p6 = os.path.join(_d, "shadow.pptx")
_repack(_p5, _p6, _shadowed)
_s6 = _OX.read(_p6)
check(_s6.theme_part == "ppt/theme/theme1.xml" and _s6.theme_effects[1] == "outerShdw 35%",
      "the master's theme and its effect styles are read")
_rep6, _ = _AP.audit(_p6)
_rows6 = [r for r in _rep6.rows if r["code"] == "raster-effect"]
check(_rows6 and _rows6[0]["level"] == "error" and "(theme)" in _rows6[0]["message"],
      "a shadow inherited from the theme is an error, despite the empty <a:effectLst/>")
_p7 = os.path.join(_d, "shadow_fixed.pptx")
_done = _AP.fix(_p6, _p7)
_rep7, _ = _AP.audit(_p7)
check(_done["effect_refs"] > 0 and _done["theme_styles"] == 3
      and not any(r["code"] == "raster-effect" for r in _rep7.rows),
      "--fix switches theme effects off (%d references)" % _done["effect_refs"])
check('<a:effectRef idx="0"' in zipfile.ZipFile(_p7).read("ppt/slides/slide1.xml").decode()
      and "outerShdw" not in zipfile.ZipFile(_p7).read("ppt/theme/theme1.xml").decode(),
      "...in the slide and in the theme")

section("v3.1 subscript placement")
check(pptx_writer.script_baseline(-1) == -17000 and pptx_writer.script_baseline(1) == 28000,
      "scripts written 15 % larger are shifted -17 % / +28 % (0.20 / 0.32 x body)")
check(pptx_writer.script_baseline(-1, 7.8 / 7.0) == -18000, "7.8 on 7.0 pt gets the measured -18 %")
check('baseline="-17000"' in _x3 and not any(r["code"] == "script-too-low"
                                              for r in _AP.audit(_p3)[0].rows),
      "the engine's own subscripts pass")
_p8 = os.path.join(_d, "low.pptx")
_repack(_p3, _p8, lambda n, x: x.replace('baseline="-17000"', 'baseline="-25000"'))
_rep8, _ = _AP.audit(_p8)
check(any(r["code"] == "script-too-low" for r in _rep8.rows),
      "-25 % on an enlarged run is caught (LibreOffice: 43 % of the script's height)")
_p9 = os.path.join(_d, "low_fixed.pptx")
_done = _AP.fix(_p8, _p9)
_rep9, _ = _AP.audit(_p9)
check(_done["subscripts"] and not any(r["code"] == "script-too-low" for r in _rep9.rows),
      "--fix raises them to %s" % sorted(set(_done["subscripts"])))
_sp = lambda t, size, x0, x1, y: {"text": t, "size": size, "bbox": (x0, y - size, x1, y),
                                   "origin": (x0, y)}
_drops = _AD._script_drops([_sp("d", 6.5, 10, 13.3, 50), _sp("1", 4.7, 13.4, 15.8, 52.0),
                            _sp("z", 6.5, 30, 33, 50), _sp("1", 4.7, 33.1, 35.5, 51.3)])
check(len(_drops) == 2 and _drops[0][0] > _AD.MAX_SUB_DROP > _drops[1][0],
      "audit_pdf measures the drop against the script's own size")
check(_AD.CAMERAS.get((1600, 900)) == "nuScenes", "a nuScenes frame is recognised as a camera photo")

section("v3.1 legends of rendered classes")
if not _IC.available():
    check(True, "(Pillow and PyMuPDF missing: picture checks skipped)")
else:
    check(_IC.share((4, [(1, (0, 0, 115)), (1, (0, 0, 230)), (2, (255, 255, 255))]), (0, 0, 230)) == 0.5,
          "a shaded tone counts for its legend colour")
    check(not _IC.saturated((128, 128, 128)) and _IC.saturated((0, 207, 191)),
          "grey legend colours are never looked for in pictures")
    _occ = os.path.join(_d, "occ.png")
    # an occupancy-like render: car orange in three shades on white
    _rgb_png(_occ, 60, 40, lambda x, y: (255, 158, 0) if x < 10 else (204, 126, 0) if x < 20
             else (255, 255, 255))
    _legspec = {"figure": {"width": 240},
                "body": [{"id": "im", "shape": "image", "src": "occ.png", "w": 60}],
                "legend": {"items": [{"text": "car", "kind": "swatch", "color": "#FF9E00"}]}}
    check("legend-unused-entry" not in _codes(dict(_legspec), base=_d),
          "validate: a legend colour drawn only in an image slot is used")
    _legspec["legend"]["items"].append({"text": "bus", "kind": "swatch", "color": "#FF7F50"})
    _c = _codes(dict(_legspec), base=_d)
    check("legend-unused-entry" in _c, "validate: a class missing from the renders is still reported")
    _legspec["legend"]["items"][1]["in_image"] = True
    check("legend-unused-entry" not in _codes(dict(_legspec), base=_d), "...unless marked in_image")
    _legspec["legend"]["items"] = _legspec["legend"]["items"][:1]
    _p10 = os.path.join(_d, "occ.pptx")
    pptx_writer.write(Figure(_legspec, base=_d), _p10)
    _rows10 = _AP.audit(_p10)[0].rows
    check(not any(r["code"] == "legend-orphan" for r in _rows10)
          and any(r["code"] == "legend-in-images" for r in _rows10),
          "audit_pptx: a legend colour drawn only in an embedded picture is not an orphan")

print("\n%s  (%d failure%s)" % ("FAILED" if FAILED else "ALL PASSED",
                                len(FAILED), "" if len(FAILED) == 1 else "s"))
sys.exit(1 if FAILED else 0)
