"""Turn a Graph into a figure spec.

The layout decision is the whole job.  A graph drawn as a graph looks like a
graph; the corpus draws pipelines as *columns of stages*, left to right, with
the branch structure carried inside a column rather than by edge crossings.
So: layer the DAG, make each layer a panel or a column, and route the rest.

Output is a plain dict, which the caller dumps as YAML.  It is meant to be
edited afterwards -- the emitter's job is a correct, boring first draft, not
a finished figure.
"""

from .graph import SHAPE_FOR_ROLE, prettify, abbreviate


MARKUP = str.maketrans({"_": r"\_", "*": r"\*", "^": r"\^"})


def esc(text):
    """Neutralise the spec's inline markup.

    Identifiers from source are full of underscores and asterisks; left raw,
    ``kernel_size=1`` renders as "kernel" with a subscript s.  Hand-written
    specs want the markup, generated ones never do.
    """
    return str(text).translate(MARKUP)


def _label(n, short=False):
    txt = esc(prettify(n.name))
    if short:
        txt = abbreviate(txt)
    if len(txt) > 22:
        # break at the last space before the midpoint so boxes stay compact
        mid = txt.rfind(" ", 0, len(txt) // 2 + 6)
        if mid > 0:
            txt = txt[:mid] + "\n" + txt[mid + 1:]
    return txt


def _node_spec(n, detail=True, short=False):
    s = {"id": n.id.replace(".", "_"), "text": _label(n, short)}
    if n.kind == "op":
        s.update(shape="circleop", op=n.meta.get("op", "+"), d=9)
        s.pop("text", None)
        return s
    shape = SHAPE_FOR_ROLE.get(n.role)
    if shape:
        s["shape"] = shape
    if n.role != "neutral":
        s["role"] = n.role
    elif n.meta.get("family"):
        s["fill"] = "%s.light" % n.meta["family"]
    if n.kind == "tensor" and n.role not in ("input", "output"):
        s.update(shape="slab", n=3, cell=7, cellh=20)
        s.pop("text", None)
        s["caption"] = _label(n, short)
    if detail and n.note:
        s["caption"] = esc(n.note)
    if n.repeat and str(n.repeat) not in ("1", "None"):
        s["badge"] = "\u00d7%s" % n.repeat
    if detail and n.cls and n.cls.lower() not in n.name.lower().replace("_", ""):
        s.setdefault("caption", esc(n.cls))
    return s


def emit(g, venue="cvpr", width="double", stages=None, detail=True,
         title=None, font="times", rows=1, short=False):
    layers = g.topo_layers()
    spec = {
        "figure": {"id": (title or g.title or "model").lower().replace(" ", "_"),
                   "venue": venue, "width": width, "font": font},
        "layout": "row",
        "gap": 13,
        "panels": [],
        "edges": [],
    }
    groups = _group(layers, rows)
    names = stages or []
    used = set()

    for gi, grp in enumerate(groups):
        body = []
        for layer in grp:
            if len(layer) == 1:
                body.append(_node_spec(layer[0], detail, short))
            else:
                body.append({"col": {"gap": 7, "align": "center",
                                     "body": [_node_spec(n, detail, short) for n in layer]}})
        panel = {"id": "stage%d" % gi, "frame": "dashed", "valign": "middle"}
        if gi < len(names):
            panel["title"] = names[gi]
        elif len(groups) > 1:
            t = _stage_name(grp)
            if t and t in used:
                t = None
            if t:
                used.add(t)
            panel["title"] = t or "Stage %d" % (gi + 1)
        panel["body"] = [{"row": {"gap": 10, "align": "center", "body": body}}] \
            if len(body) > 1 else body
        spec["panels"].append(panel)

    seen = set()
    for a, b, lab in g.edges:
        na, nb = g.get(a), g.get(b)
        if not na or not nb:
            continue
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        e = {"from": a.replace(".", "_"), "to": b.replace(".", "_")}
        if lab:
            e["label"] = lab
        spec["edges"].append(e)
    _declare_operators(g, spec)
    return spec


_OP_MEANING = {"+": ("\\oplus", "element-wise addition"),
               "x": ("\\otimes", "element-wise product"),
               "c": ("c", "channel concatenation")}


def _declare_operators(g, spec):
    """Every operator glyph the draft draws is a symbol a reader must be told
    about: declare it, and start a caption that defines it, so the draft
    passes the symbol check and the author only has to finish the sentence."""
    ops = []
    for n in g.nodes:
        if n.kind == "op":
            op = n.meta.get("op", "+")
            if op not in ops:
                ops.append(op)
    if not ops:
        return
    sym, defs = {}, []
    for op in ops:
        key, meaning = _OP_MEANING.get(op, (op, "operator"))
        sym[key] = {"meaning": meaning, "defined_in": "caption"}
        glyph = "\\textcircled{c}" if op == "c" else "$%s$" % key.replace("\\\\", "\\")
        defs.append("%s: %s" % (glyph, meaning))
    spec["symbols"] = sym
    spec["caption"] = ("TODO: the claim this figure makes. " + "; ".join(defs) + ".")



def _group(layers, rows=1):
    """Split the layered DAG into stage panels at role boundaries.

    A stage panel is a *phase* of the method, and the corpus draws phases, not
    depth levels: SparseWorld's four panels cover 14 boxes.  So the cut points
    are where the dominant role changes, with a cap so no panel swallows the
    figure.
    """
    if not layers:
        return []
    cap = max(2, (len(layers) + 3) // 4)
    groups, cur, cur_role = [], [], None

    def dom(layer):
        rs = [n.role for n in layer if n.role != "neutral"]
        return rs[0] if rs else None

    for layer in layers:
        r = dom(layer)
        if cur and ((r and cur_role and r != cur_role) or len(cur) >= cap):
            groups.append(cur)
            cur, cur_role = [], None
        cur.append(layer)
        cur_role = cur_role or r
    if cur:
        groups.append(cur)
    if rows > 1:
        return groups
    return groups


# Panel titles the emitter is willing to assert on its own.
#
# `core` deliberately does *not* say "Our Contribution".  The role came from a
# name pattern -- `pts_voxel_layer` matches it -- and a draft that labels a
# guessed module as the paper's contribution is a claim the reader may ship by
# accident.  Marking the contribution is a decision for the author; pass
# --stages, or set `role: core` and retitle the panel by hand.
STAGE_WORDS = {
    "input": "Input", "backbone": "Feature Extraction", "encoder": "Encoding",
    "attention": "Attention", "core": "Core Module", "temporal": "Temporal",
    "memory": "Memory", "decoder": "Decoding", "output": "Output", "loss": "Objective",
}


def _stage_name(grp):
    roles = [n.role for layer in grp for n in layer]
    for r in ("core", "attention", "temporal", "memory", "decoder", "backbone",
              "encoder", "output", "input"):
        if r in roles:
            return STAGE_WORDS[r]
    return None


# --------------------------------------------------------------------------
# Fitting.
#
# A draft that overflows its column gets silently downscaled at render time,
# and downscaling is exactly how figures end up with 5 pt labels -- the single
# most common reviewer complaint and the thing this project exists to stop.
# So the emitter renders its own draft, measures it, and reshapes until the
# type lands at full size.
# --------------------------------------------------------------------------

def fit(g, venue="cvpr", width="double", min_scale=0.94, **kw):
    """Emit a spec that fits its column, reshaping the draft until it does.

    Returns ``(spec, note)`` where *note* explains any concession made.
    """
    from ..figure import Figure

    def measure(spec):
        try:
            f = Figure(spec)
            return f.fit_scale, f
        except Exception:
            return 1.0, None

    spec = emit(g, venue=venue, width=width, **kw)
    scale, _ = measure(spec)
    if scale >= min_scale:
        return spec, None
    notes = []

    # 1. Drop the channel-count captions: they are the widest thing per box.
    if kw.get("detail", True):
        kw = dict(kw, detail=False)
        spec = emit(g, venue=venue, width=width, **kw)
        scale, _ = measure(spec)
        notes.append("dropped channel captions")
        if scale >= min_scale:
            return spec, "; ".join(notes) + " to fit the column"

    # 2. Use the corpus's own shorter names for the same modules.
    kw = dict(kw, short=True)
    spec = emit(g, venue=venue, width=width, **kw)
    scale, _ = measure(spec)
    notes.append("abbreviated module names")
    if scale >= min_scale:
        return spec, "; ".join(notes) + " to fit the column"

    # 3. Shed the least connected boxes -- but only a few.  A short row that
    #    fits beats a long row downscaled into unreadable type; a *five*-box
    #    row does not beat an eleven-box figure in two bands, and eleven is
    #    the corpus median for a framework figure.  So shedding stops at 25%
    #    of the graph and the wrap takes over.
    n0 = len(g.nodes)
    floor = max(6, int(round(n0 * 0.75)))
    while len(g.nodes) > floor:
        g.limit(len(g.nodes) - 1)
        spec = emit(g, venue=venue, width=width, **kw)
        scale, _ = measure(spec)
        if scale >= min_scale:
            notes.append("dropped %d peripheral modules" % (n0 - len(g.nodes)))
            return spec, "; ".join(notes) + " to fit the column"

    # 4. Last resort: wrap into two bands with an explicit return sweep.
    two = _banded(g, venue=venue, width=width, bands=2, **kw)
    notes.append("wrapped into two rows")
    return two, "; ".join(notes) + " to fit the column"


def _banded(g, bands=2, **kw):
    """Emit as *bands* stacked left-to-right rows instead of one long row."""
    spec = emit(g, **kw)
    panels = spec.pop("panels")
    per = (len(panels) + bands - 1) // bands
    rows = [panels[i:i + per] for i in range(0, len(panels), per)]
    spec["layout"] = "col"
    spec["gap"] = 11
    spec["panels"] = [
        {"id": "band%d" % i, "frame": "none", "valign": "middle",
         "body": [{"row": {"gap": 13, "align": "center",
                           "body": [_as_group(p) for p in band]}}]}
        for i, band in enumerate(rows)]

    # The edge that crosses the wrap has to read as a fold: out of the *bottom*
    # of the upper band's last box, into the *top* of the lower band's first.
    #
    # `mid` matters here.  At the default 0.5 the horizontal leg runs halfway
    # down the band gap -- which is exactly where the lower band's stage titles
    # sit, so the connector draws a line through them.  Pushing it to 0.95 puts
    # the leg just above the target box, below the titles.
    order = [n.id.replace(".", "_") for layer in g.topo_layers() for n in layer]
    last_of_band = {}
    idx = 0
    for i, band in enumerate(rows):
        n = sum(len(_ids(p)) for p in band)
        last_of_band[i] = order[idx:idx + n]
        idx += n
    for e in spec["edges"]:
        for i in range(len(rows) - 1):
            up, dn = last_of_band.get(i, []), last_of_band.get(i + 1, [])
            if up and dn and e["from"] == up[-1] and e["to"] == dn[0]:
                e["from"] = up[-1] + ".s"
                e["to"] = dn[0] + ".n"
                e["mid"] = 0.95
    return spec


def _ids(panel):
    out = []
    def walk(x):
        if isinstance(x, dict):
            if "id" in x and not any(k in x for k in ("row", "col", "group", "body")):
                out.append(x["id"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(panel.get("body"))
    return out


def _as_group(panel):
    g = {"id": panel["id"], "frame": panel.get("frame", "dashed"),
         "valign": "middle", "body": panel["body"]}
    if panel.get("title"):
        g["title"] = panel["title"]
    return {"group": g}
