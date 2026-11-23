"""Read an architecture out of an mmengine / mmdet / mmdet3d config.

Where one exists this is a much better source than the model source: the
config names every stage, gives its class and its channel widths, and lists
them in pipeline order.  Most of the driving and detection literature in the
reference corpus ships one.

Parsed with ``ast`` -- these configs are executable Python and are not run.
"""

import ast
import os
import re

from .graph import Graph, Node, infer_role, prettify

# Config keys in the order they feed each other.  mmdet-family configs are
# written in this order by convention, and where they are not, the key names
# still say what stage they are.
# Pipeline order.  mmdet-family configs are conventionally written in this
# order, but not reliably, and a key the list does not know sorts to the end --
# which put `pts_bbox_head` in the middle of a figure during testing.  So the
# list is grouped by *phase* and covers the keys that actually occur across the
# 48 repositories in the reference corpus.
STAGE_ORDER = [
    # 0. input handling
    "data_preprocessor", "pts_voxel_layer", "pts_pillar_layer", "voxel_layer",
    # 1. per-modality encoders
    "img_backbone", "backbone", "pts_voxel_encoder", "voxel_encoder",
    "pts_middle_encoder", "middle_encoder", "pts_backbone",
    # 2. necks
    "img_neck", "neck", "pts_neck",
    # 3. lift / project into the shared space
    "img_view_transformer", "view_transformer", "transformer",
    "depth_net", "pre_process",
    # 4. fusion
    "occ_fuser", "fusion_layer", "fuser", "pts_fusion_layer",
    # 5. shared encoder
    "img_bev_encoder_backbone", "occ_encoder_backbone", "bev_encoder_backbone",
    "encoder", "img_bev_encoder_neck", "occ_encoder_neck", "bev_encoder_neck",
    # 6. heads
    "decoder", "occupancy_head", "occ_head", "nerf_head", "dense_nerf_head",
    "pts_bbox_head", "bbox_head", "roi_head", "seg_head", "head",
    "loss", "train_cfg", "test_cfg",
]
SKIP = re.compile(r"^(train_cfg|test_cfg|init_cfg|data_preprocessor|"
                  r"pretrained|type|.*_weight|.*_cfg)$")


def _lit(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _dict_of(call_or_dict):
    """Normalise `dict(type='X', a=1)` and `{'type': 'X'}` to a plain dict."""
    if isinstance(call_or_dict, ast.Call):
        fn = call_or_dict.func
        nm = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if nm != "dict":
            return None
        return {k.arg: k.value for k in call_or_dict.keywords if k.arg}
    if isinstance(call_or_dict, ast.Dict):
        out = {}
        for k, v in zip(call_or_dict.keys, call_or_dict.values):
            key = _lit(k)
            if isinstance(key, str):
                out[key] = v
        return out
    return None


def _note(d):
    """The one or two numbers worth printing under a stage name.

    Ordered by what a reader of this literature actually wants: how deep, then
    how wide.  Two items is the cap -- a caption longer than its box is what
    forces the whole figure to downscale.
    """
    LABELS = [
        ("depth", "depth"), ("depths", "depth"), ("num_layer", "layers"),
        ("num_layers", "layers"), ("embed_dims", "dim"), ("hidden_dim", "dim"),
        ("in_channels", "in"), ("out_channels", "out"), ("num_channels", "ch"),
        ("channels", "ch"), ("num_heads", "heads"), ("num_query", "queries"),
        ("num_queries", "queries"), ("num_classes", "classes"),
    ]
    bits = []
    for key, label in LABELS:
        if key not in d:
            continue
        v = _lit(d[key])
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            if len(v) > 4:
                continue
            v = "/".join(str(x) for x in v)
        bits.append("%s %s" % (label, v))
        if len(bits) >= 2:
            break
    return ", ".join(bits) or None


def find_model(path):
    tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read(), path)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id in ("model", "_model"):
                    return _dict_of(stmt.value)
    return None


def build(path, max_nodes=14, depth=1, keep_loss=False):
    d = find_model(path)
    if d is None:
        raise SystemExit("no top-level `model = dict(...)` in %s" % path)
    top = _lit(d.get("type")) if "type" in d else None
    g = Graph(title=prettify(top or os.path.basename(path).rsplit(".", 1)[0]))

    keys = [k for k in d if not SKIP.match(k)]
    keys.sort(key=lambda k: (STAGE_ORDER.index(k) if k in STAGE_ORDER else 500, k))

    prev = None
    for k in keys:
        sub = _dict_of(d[k])
        if sub is None:
            continue
        cls = _lit(sub.get("type")) or ""
        nid = k
        g.add(Node(nid, k, cls, note=_note(sub),
                   meta={"cls": cls}))
        if prev:
            g.link(prev, nid)
        prev = nid
        if depth > 1:
            for k2 in sub:
                if SKIP.match(k2):
                    continue
                s2 = _dict_of(sub[k2])
                if s2 is None:
                    continue
                cid = "%s.%s" % (k, k2)
                g.add(Node(cid, k2, _lit(s2.get("type")) or "",
                           note=_note(s2), meta={"parent": nid}))
                g.link(cid, nid)
    if not g.nodes:
        raise SystemExit("model dict in %s has no sub-module entries" % path)
    # A config lists stages but no explicit input/output; the figure needs them.
    if not keep_loss:
        for n in list(g.nodes):
            if n.role == "loss":
                g.drop(n.id, bridge=False)
    g.add(Node("__in__", "input", "", role="input", kind="tensor"))
    g.link("__in__", g.nodes[0].id)
    g.limit(max_nodes)
    g.color_branches()
    return g, top or "model"
