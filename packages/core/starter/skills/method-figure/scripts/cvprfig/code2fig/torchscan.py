"""Read a PyTorch model's architecture out of its source, without importing it.

Importing a research repo to introspect a model means installing its whole
dependency tree, downloading checkpoints and, often, a CUDA build.  This
module never executes the code: it parses with ``ast``, collects the
submodules assigned in ``__init__`` and recovers the dataflow by walking
``forward`` and tracking which variable each call writes to.

That is less precise than tracing a real tensor, and it is honest about it:
control flow it cannot resolve becomes a ``xN`` badge or is dropped, and the
CLI reports what it skipped.
"""

import ast
import os
import re
import warnings

from .graph import Graph, Node, infer_role, prettify

FORWARD_NAMES = ("forward", "forward_train", "_forward", "forward_single",
                 "simple_test", "extract_feat", "call")

TORCH_LEAF = re.compile(
    r"^(nn|torch\.nn|F|torch\.nn\.functional)\.", re.I)


def _name_of(node):
    """Dotted name of a call target: `self.backbone` -> 'self.backbone'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return "%s.%s" % (base, node.attr) if base else node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return None


def _int_args(call):
    """Positional ints and int keywords -- the channel counts worth showing."""
    out = []
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, int):
            out.append(str(a.value))
    kw = []
    for k in call.keywords:
        if k.arg and isinstance(k.value, ast.Constant) and isinstance(k.value.value, int):
            if re.search(r"channel|dim|depth|head|layer|size|width", k.arg, re.I):
                kw.append("%s=%d" % (k.arg, k.value.value))
    return out, kw


class ModuleDef(object):
    def __init__(self, name, bases, node, path):
        self.name = name
        self.bases = bases
        self.node = node
        self.path = path
        self.submodules = {}        # attr -> (class_name, note)
        self.forward = None


def collect(paths):
    """Parse every .py under *paths* and index the classes that look like models."""
    defs = {}
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs
                           if d not in (".git", "__pycache__", "build", "dist", "docs")]
                files.extend(os.path.join(root, n) for n in names if n.endswith(".py"))
        elif p.endswith(".py"):
            files.append(p)
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
            # Research repos are full of docstrings with invalid escapes
            # (\infty, \times). That is the scanned project's business, not
            # ours, and its warnings must not pollute our output.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(src, f)
        except (SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.ClassDef):
                continue
            bases = [b for b in (_name_of(b) for b in n.bases) if b]
            d = ModuleDef(n.name, bases, n, f)
            _fill(d)
            if d.submodules or d.forward:
                defs.setdefault(n.name, d)
    return defs


def _fill(d):
    for fn in d.node.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "__init__":
            _init_submodules(d, fn)
        elif fn.name in FORWARD_NAMES and d.forward is None:
            d.forward = fn
        elif fn.name in FORWARD_NAMES and fn.name == "forward":
            d.forward = fn


def _init_submodules(d, fn):
    for stmt in ast.walk(fn):
        if not isinstance(stmt, ast.Assign):
            continue
        for tgt in stmt.targets:
            if not (isinstance(tgt, ast.Attribute) and
                    isinstance(tgt.value, ast.Name) and tgt.value.id == "self"):
                continue
            val = stmt.value
            if not isinstance(val, ast.Call):
                continue
            cls = _name_of(val.func) or ""
            short = cls.rsplit(".", 1)[-1]
            pos, kw = _int_args(val)
            note = ", ".join(kw[:2]) or None
            if short == "Sequential":
                inner = [(_name_of(a.func) or "").rsplit(".", 1)[-1]
                         for a in val.args if isinstance(a, ast.Call)]
                inner = [i for i in inner if i]
                if inner:
                    note = " - ".join(inner[:3]) + (" ..." if len(inner) > 3 else "")
            if short in ("ModuleList", "Sequential") and val.args:
                first = val.args[0]
                if isinstance(first, (ast.ListComp, ast.List)):
                    note = note or "stack"
            d.submodules[tgt.attr] = (short, note)


def _repeat_of(stmt):
    """`for i in range(N)` / `for blk in self.blocks` -> a repeat count or None."""
    if not isinstance(stmt, ast.For):
        return None
    it = stmt.iter
    if isinstance(it, ast.Call) and (_name_of(it.func) or "").endswith("range"):
        if it.args and isinstance(it.args[0], ast.Constant):
            return str(it.args[0].value)
        if it.args:
            return _name_of(it.args[0]) or "N"
    n = _name_of(it)
    return "N" if n else None


def dataflow(d, defs, g, prefix="", depth=0, budget=None):
    """Walk `forward` and add nodes/edges to *g*.  Returns the output var ids."""
    if d.forward is None or depth > 2:
        return []
    produced = {}                    # variable name -> node id
    fn = d.forward
    for a in fn.args.args[1:]:       # skip self
        vid = prefix + a.arg
        g.add(Node(vid, a.arg, "", role=infer_role(a.arg), kind="tensor"))
        produced[a.arg] = vid

    def call_targets(expr):
        """All `self.X(...)` and tensor ops inside an expression, in order."""
        hits = []
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call):
                nm = _name_of(sub.func) or ""
                if nm.startswith("self."):
                    attr = nm.split(".", 2)[1]
                    if attr in d.submodules:
                        hits.append(("mod", attr, sub))
                elif nm in ("torch.cat", "cat", "torch.stack", "stack"):
                    hits.append(("op", "cat", sub))
        return hits

    def inputs_of(expr):
        return [produced[n.id] for n in ast.walk(expr)
                if isinstance(n, ast.Name) and n.id in produced]

    def emit(stmt, repeat=None):
        if isinstance(stmt, (ast.For, ast.While)):
            rep = _repeat_of(stmt) or repeat
            for s in stmt.body:
                emit(s, rep)
            return
        if isinstance(stmt, ast.If):
            for s in stmt.body:
                emit(s, repeat)
            return
        if isinstance(stmt, ast.Return):
            if stmt.value is not None:
                outs = inputs_of(stmt.value)
                if outs:
                    oid = prefix + "__out__"
                    g.add(Node(oid, "output", "", role="output", kind="tensor"))
                    for o in outs[:2]:
                        g.link(o, oid)
            return
        if not isinstance(stmt, (ast.Assign, ast.AugAssign, ast.Expr)):
            return
        value = stmt.value
        hits = call_targets(value)
        srcs = inputs_of(value)
        last = None
        for kind, attr, call in hits:
            if kind == "op":
                nid = "%sop_cat_%d" % (prefix, call.lineno)
                g.add(Node(nid, "concat", "", role="neutral", kind="op",
                           meta={"op": "c"}))
            else:
                cls, note = d.submodules[attr]
                nid = prefix + attr
                if g.get(nid) is None:
                    g.add(Node(nid, attr, cls, note=note,
                               repeat=repeat or 1, kind="module"))
                elif repeat and g.get(nid).repeat == 1:
                    g.get(nid).repeat = repeat
            for s in srcs:
                g.link(s, nid)
            if last:
                g.link(last, nid)
            last = nid
        if last is None:
            return
        tgts = stmt.targets if isinstance(stmt, ast.Assign) else [getattr(stmt, "target", None)]
        for t in tgts:
            if isinstance(t, ast.Name):
                produced[t.id] = last
            elif isinstance(t, ast.Tuple):
                for el in t.elts:
                    if isinstance(el, ast.Name):
                        produced[el.id] = last
        if isinstance(stmt, ast.AugAssign):
            # x = x + self.block(x) -- a residual join
            nid = "%sres_%d" % (prefix, stmt.lineno)
            g.add(Node(nid, "add", "", role="neutral", kind="op", meta={"op": "+"}))
            for s in srcs:
                g.link(s, nid)
            g.link(last, nid)
            if isinstance(stmt.target, ast.Name):
                produced[stmt.target.id] = nid

    for stmt in fn.body:
        emit(stmt)
    return list(produced.values())


def build(paths, model=None, max_nodes=14, keep_loss=False):
    """Top-level: source paths -> a figure-sized Graph."""
    defs = collect(paths)
    if not defs:
        raise SystemExit("no class with submodules or a forward() found under %s"
                         % ", ".join(paths))
    d = _pick(defs, model)
    g = Graph(title=prettify(d.name))
    # Inherited submodules: a detector usually declares half its stack in a base.
    chain = _mro(d, defs)
    merged = {}
    for anc in reversed(chain):
        merged.update(anc.submodules)
    d = _clone_with(d, merged)
    dataflow(d, defs, g)
    # Anything declared but never seen in forward still belongs on the figure
    # if forward was unreadable; otherwise it is a helper.
    if not g.edges:
        prev = None
        for attr, (cls, note) in merged.items():
            nid = attr
            g.add(Node(nid, attr, cls, note=note))
            if prev:
                g.link(prev, nid)
            prev = nid
    g.prune_noise()
    if not keep_loss:
        for n in list(g.nodes):
            if n.role == "loss":
                g.drop(n.id, bridge=False)
    g.prune_isolated()
    g.limit(max_nodes)
    g.color_branches()
    return g, d.name, sorted(defs)


def _clone_with(d, subs):
    nd = ModuleDef(d.name, d.bases, d.node, d.path)
    nd.submodules = subs
    nd.forward = d.forward
    return nd


def _mro(d, defs, seen=None):
    seen = seen or set()
    out = [d]
    for b in d.bases:
        short = b.rsplit(".", 1)[-1]
        if short in defs and short not in seen:
            seen.add(short)
            out.extend(_mro(defs[short], defs, seen))
    return out


def _pick(defs, model):
    if model:
        if model in defs:
            return defs[model]
        raise SystemExit("class %r not found.  Available: %s"
                         % (model, ", ".join(sorted(defs)[:40])))
    # Heuristic: the model is the class with the most submodules that nothing
    # else inherits from -- i.e. the top of the tree.
    inherited = {b.rsplit(".", 1)[-1] for d in defs.values() for b in d.bases}
    roots = [d for k, d in defs.items() if k not in inherited] or list(defs.values())
    roots.sort(key=lambda d: (len(d.submodules), d.forward is not None), reverse=True)
    return roots[0]
