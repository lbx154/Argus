"""Write a spec dict back out as readable YAML.

The bundled reader (miniyaml) is a strict subset, so the writer is too: block
mappings, block sequences, flow mappings for short leaf nodes.  Keeping leaf
nodes on one line matters -- a generated spec is meant to be edited by hand,
and one box per line is how the hand-written templates read.
"""

import re

PLAIN = re.compile(r"^[A-Za-z_][\w./+-]*$")
KEY_ORDER = ["id", "shape", "text", "role", "fill", "stroke", "n", "cell", "cellh",
             "grid", "side", "d", "op", "src", "srcs", "w", "h", "badge", "caption"]


def _scalar(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if "\n" in s or not PLAIN.match(s) or s in ("true", "false", "null", "on", "off"):
        return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return s


def _is_leaf(d):
    return isinstance(d, dict) and all(
        not isinstance(v, (dict, list)) for v in d.values()) and len(d) <= 8


def _flow(d):
    keys = sorted(d, key=lambda k: (KEY_ORDER.index(k) if k in KEY_ORDER else 99, k))
    return "{%s}" % ", ".join("%s: %s" % (k, _scalar(d[k])) for k in keys)


def dump(obj, indent=0):
    pad = "  " * indent
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                if _is_leaf(v):
                    out.append("%s%s: %s" % (pad, k, _flow(v)))
                else:
                    out.append("%s%s:" % (pad, k))
                    out.append(dump(v, indent + 1))
            elif isinstance(v, list):
                if not v:
                    out.append("%s%s: []" % (pad, k))
                else:
                    out.append("%s%s:" % (pad, k))
                    out.append(dump(v, indent + 1))
            else:
                out.append("%s%s: %s" % (pad, k, _scalar(v)))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and _is_leaf(item):
                out.append("%s- %s" % (pad, _flow(item)))
            elif isinstance(item, (dict, list)):
                block = dump(item, indent + 1)
                first, rest = block.split("\n", 1) if "\n" in block else (block, "")
                out.append("%s- %s" % (pad, first.lstrip()))
                if rest:
                    out.append(rest)
            else:
                out.append("%s- %s" % (pad, _scalar(item)))
    else:
        out.append("%s%s" % (pad, _scalar(obj)))
    return "\n".join(x for x in out if x.strip())
