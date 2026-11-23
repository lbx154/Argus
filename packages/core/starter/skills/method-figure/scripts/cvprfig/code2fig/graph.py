"""The intermediate representation between source code and a figure spec.

A ``Graph`` is deliberately poorer than the code it came from.  A figure that
faithfully mirrors a 40-module ``nn.Module`` tree is unreadable; the corpus
median framework figure has 11 labelled boxes.  So the pipeline is

    source -> Graph (everything) -> prune/merge -> Graph (figure-sized) -> spec

and the pruning rules live here, in one place, where they can be argued with.
"""

import re


# Role inference.  Ordered: the first pattern that matches a module's name or
# class wins.  These names come from counting identifier frequency across the
# 48 repositories in the reference corpus, so they cover what this literature
# actually calls things rather than what a textbook would.
ROLE_RULES = [
    ("loss",      r"loss|criterion|objective"),
    ("input",     r"^(img|image|input|data|sample|obs|frame|point|pts|lidar|radar)s?$|preprocess|tokeniz|patch_embed|stem$"),
    ("backbone",  r"backbone|resnet|swin|vit|convnext|efficientnet|vovnet|encoder_2d|img_encoder"),
    ("encoder",   r"encoder|neck|fpn|pyramid|aggregat|embed"),
    ("attention", r"atten|attn|transformer|mhsa|mha|cross_?attn|self_?attn|deform"),
    ("temporal",  r"temporal|recurr|lstm|gru|rnn|memory_?bank|history|prev|stream|mamba|ssm"),
    ("memory",    r"memory|bank|queue|cache|buffer|prior"),
    ("core",      r"view_?transform|lift|splat|bev|voxel|gaussian|occupanc|world_?model|render"),
    ("decoder",   r"decoder|head|predictor|classifier|regressor|seg_?head|det_?head|output_?layer"),
    ("output",    r"^(out|output|pred|prediction|logit|result)s?$"),
    ("aux",       r"aux|discriminator|proj|adapter|refine"),
]

# Modules that are plumbing, not architecture.  Dropping them is what takes a
# graph from "accurate" to "legible".
NOISE = re.compile(
    r"^(norm|bn\d*|ln|gn|layer_?norm|batch_?norm|group_?norm|dropout|drop_?path|"
    r"act|relu|gelu|silu|sigmoid|softmax|softplus|tanh|identity|flatten|"
    r"pool|avgpool|maxpool|upsample|downsample|interp|pad|reshape|permute|"
    r"init_\w+|_\w+)$", re.I)

SHAPE_FOR_ROLE = {
    "input": "image", "backbone": "trapezoid", "encoder": "trapezoid",
    "decoder": "invtrapezoid", "output": "image", "loss": "box",
}


def infer_role(name, cls=""):
    hay = ("%s %s" % (name, cls)).lower()
    for role, pat in ROLE_RULES:
        if re.search(pat, hay):
            return role
    return "neutral"


def prettify(name):
    """`img_bev_encoder_neck` -> `Img BEV Encoder Neck`, keeping known acronyms."""
    ACR = {"bev", "fpn", "mlp", "cnn", "rnn", "lstm", "gru", "mha", "mhsa", "vit",
           "rgb", "lidar", "3d", "2d", "1d", "nerf", "gt", "roi", "iou", "nms",
           "ssm", "kv", "qkv", "ffn", "pe", "lss", "sdf", "occ"}
    parts = re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", name.strip("_"))
    out = []
    for p in parts:
        if not p:
            continue
        out.append(p.upper() if p.lower() in ACR else p[:1].upper() + p[1:])
    return " ".join(out) or name


class Node(object):
    __slots__ = ("id", "name", "cls", "role", "note", "repeat", "kind", "meta")

    def __init__(self, id, name, cls="", role=None, note=None, repeat=1,
                 kind="module", meta=None):
        self.id = id
        self.name = name
        self.cls = cls
        self.role = role or infer_role(name, cls)
        self.note = note            # channel counts, depths -- the small print
        self.repeat = repeat        # ``xN`` badge
        self.kind = kind            # module | op | tensor
        self.meta = meta or {}

    def __repr__(self):
        return "<%s %s:%s>" % (self.kind, self.id, self.role)


class Graph(object):
    def __init__(self, title=""):
        self.title = title
        self.nodes = []
        self.edges = []             # (src_id, dst_id, label|None)
        self._by_id = {}

    def add(self, node):
        if node.id in self._by_id:
            return self._by_id[node.id]
        self.nodes.append(node)
        self._by_id[node.id] = node
        return node

    def link(self, a, b, label=None):
        if a == b or a not in self._by_id or b not in self._by_id:
            return
        if (a, b, label) not in self.edges:
            self.edges.append((a, b, label))

    def get(self, nid):
        return self._by_id.get(nid)

    # ------------------------------------------------------------ pruning
    def drop(self, nid, bridge=True):
        """Remove a node.  ``bridge`` reconnects its inputs to its outputs."""
        if nid not in self._by_id:
            return
        ins = [a for a, b, _ in self.edges if b == nid]
        outs = [b for a, b, _ in self.edges if a == nid]
        self.edges = [e for e in self.edges if nid not in (e[0], e[1])]
        if bridge:
            for a in ins:
                for b in outs:
                    self.link(a, b)
        self.nodes = [n for n in self.nodes if n.id != nid]
        del self._by_id[nid]

    def prune_noise(self):
        for n in list(self.nodes):
            if n.kind == "module" and NOISE.match(n.name):
                self.drop(n.id)
        return self

    def prune_isolated(self):
        """Drop modules that never appear in the dataflow -- usually helpers."""
        used = {a for a, _, _ in self.edges} | {b for _, b, _ in self.edges}
        if not used:
            return self
        for n in list(self.nodes):
            if n.id not in used and n.kind == "module":
                self.drop(n.id, bridge=False)
        return self

    def limit(self, k):
        """Keep the k most important nodes.  Importance = degree, with the
        paper's own contribution (role ``core``) and the endpoints protected."""
        if len(self.nodes) <= k:
            return self
        deg = {}
        for a, b, _ in self.edges:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        PRIORITY = {"core": 6, "input": 5, "output": 5, "backbone": 4,
                    "decoder": 4, "attention": 3, "encoder": 3, "temporal": 3,
                    "memory": 2, "aux": 1, "neutral": 0, "loss": 0}
        rank = sorted(self.nodes,
                      key=lambda n: (PRIORITY.get(n.role, 0), deg.get(n.id, 0)),
                      reverse=True)
        for n in rank[k:]:
            self.drop(n.id)
        return self

    def topo_layers(self):
        """Longest-path layering -- gives left-to-right stage columns."""
        depth = {n.id: 0 for n in self.nodes}
        for _ in range(len(self.nodes)):
            changed = False
            for a, b, _ in self.edges:
                if a in depth and b in depth and depth[b] < depth[a] + 1:
                    depth[b] = depth[a] + 1
                    changed = True
            if not changed:
                break
        layers = {}
        for n in self.nodes:
            layers.setdefault(depth[n.id], []).append(n)
        return [layers[k] for k in sorted(layers)]

    def color_branches(self, families=("blue", "green", "orange", "purple")):
        """Give parallel branches distinct families.

        Role inference covers the named stages -- backbone, head, attention --
        but a module called ``context_mlp`` is just a module, and a figure
        where every box is grey tells the reader nothing.  The corpus colours
        by *branch*: everything downstream of a fork shares a tint until the
        branches rejoin.  Only applied when role inference left the figure
        mostly colourless.
        """
        coloured = [n for n in self.nodes if n.role != "neutral"]
        if len(coloured) > 0.55 * max(len(self.nodes), 1):
            return self
        succ = {}
        for a, b, _ in self.edges:
            succ.setdefault(a, []).append(b)
        forks = [nid for nid, outs in succ.items() if len(set(outs)) > 1]
        if not forks:
            return self
        for nid in forks:
            for i, start in enumerate(dict.fromkeys(succ[nid])):
                fam = families[i % len(families)]
                seen, stack = set(), [start]
                while stack:
                    cur = stack.pop()
                    if cur in seen:
                        continue
                    seen.add(cur)
                    node = self.get(cur)
                    if node is None or node.kind != "module":
                        continue
                    if node.role == "neutral" and "family" not in node.meta:
                        node.meta["family"] = fam
                    outs = succ.get(cur, [])
                    # stop at a join: two branches meeting should not inherit
                    if len(outs) == 1:
                        preds = [a for a, b, _ in self.edges if b == outs[0]]
                        if len(set(preds)) > 1:
                            continue
                    stack.extend(outs)
        return self

    def summary(self):
        return "%d nodes, %d edges, %d layers" % (
            len(self.nodes), len(self.edges), len(self.topo_layers()))


# Corpus label conventions.  These names are how the reference papers write
# the same modules in their figures -- "Img BEV Encoder Backbone" in the code
# is "BEV Encoder" on the page.  Applied only when a draft needs to be
# narrower, never silently.
ABBREV = [
    (r"^img[_\s]", ""), (r"^pts[_\s]", "Point "), (r"[_\s]module$", ""),
    (r"[_\s]layer$", ""), (r"[_\s]net$", ""), (r"^custom[_\s]", ""),
    (r"transformer", "Transf."), (r"\bencoder\b", "Enc."),
    (r"\bdecoder\b", "Dec."), (r"\bbackbone\b", "Backbone"),
    (r"\battention\b", "Attn."), (r"\bconvolution\b", "Conv"),
    (r"\bnormalization\b", "Norm"), (r"\bprediction\b", "Pred."),
    (r"\bprocess(ing)?\b", "Proc."), (r"\bembedding\b", "Embed."),
    (r"\bfeature(s)?\b", "Feat."), (r"\bmulti[_\s]scale\b", "M.S."),
    (r"\bpositional\b", "Pos."), (r"\btemporal\b", "Temp."),
]


def abbreviate(text):
    out = text
    for pat, rep in ABBREV:
        out = re.sub(pat, rep, out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip()
    return out or text
