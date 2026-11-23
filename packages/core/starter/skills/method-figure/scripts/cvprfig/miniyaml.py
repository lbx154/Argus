"""A dependency-free loader for the YAML subset the figure specs use.

PyYAML is used when it is importable; otherwise this parser handles block
mappings, block sequences, inline ``{}``/``[]`` flow collections, quoted and
plain scalars, comments and ``---`` document markers.  That covers every
construct the spec language allows, so the skill runs on a bare interpreter.
"""

import json
import re

try:  # pragma: no cover - environment dependent
    import yaml as _pyyaml
except Exception:  # pragma: no cover
    _pyyaml = None

_LOADER = None
if _pyyaml is not None:  # pragma: no cover - environment dependent
    class _SpecLoader(_pyyaml.SafeLoader):
        """SafeLoader without YAML 1.1's on/off/yes/no booleans.

        Figure specs use short identifiers, and ``off``, ``on``, ``no``, ``y``
        and ``n`` are all plausible node ids.  Under the stock resolver they
        silently become booleans and every edge referencing them breaks, so
        only the explicit ``true``/``false`` spellings stay boolean here.
        """

    _KEEP = ("true", "True", "TRUE", "false", "False", "FALSE")
    for _ch, _resolvers in list(_SpecLoader.yaml_implicit_resolvers.items()):
        _kept = [(t, r) for (t, r) in _resolvers
                 if t != "tag:yaml.org,2002:bool" or _ch in "tTfF"]
        _SpecLoader.yaml_implicit_resolvers[_ch] = _kept
    _LOADER = _SpecLoader


def load(source):
    if _pyyaml is not None:
        return _pyyaml.load(source, Loader=_LOADER)
    return _parse(source)


def load_path(path):
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    if path.endswith(".json"):
        return json.loads(raw)
    return load(raw)


# ---------------------------------------------------------------- scalars
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "e": "\x1b", "\\": "\\", '"': '"', "/": "/",
            " ": " ", "N": "\x85", "_": "\xa0"}


def _unescape(body):
    """Resolve the escapes a YAML double-quoted scalar may contain.

    ``\\uXXXX`` and ``\\UXXXXXXXX`` matter in practice: figure labels carry Greek
    letters and maths symbols, and a spec written for PyYAML must mean the same
    thing here.  Anything unrecognised is left alone rather than dropped.
    """
    out, i, n = [], 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1]
        if nxt in ("x", "u", "U"):
            width = {"x": 2, "u": 4, "U": 8}[nxt]
            digits = body[i + 2:i + 2 + width]
            if len(digits) == width:
                try:
                    out.append(chr(int(digits, 16)))
                    i += 2 + width
                    continue
                except ValueError:
                    pass
            out.append(ch)
            i += 1
            continue
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _scalar(tok):
    tok = tok.strip()
    if not tok:
        return None
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        body = tok[1:-1]
        if tok[0] == '"':
            body = _unescape(body)
        else:
            body = body.replace("''", "'")
        return body
    low = tok.lower()
    if low in ("null", "~") or tok == "~":
        # note: bare ``none`` stays a string, matching PyYAML -- specs use it
        # as a value (``frame: none``, ``outline: none``)
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _NUM.match(tok):
        return float(tok) if any(c in tok for c in ".eE") else int(tok)
    return tok.replace("\\n", "\n")


def _split_flow(body):
    """Split a flow collection body on top-level commas."""
    parts, depth, cur, quote = [], 0, [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch; cur.append(ch); continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur)); cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _flow(tok):
    tok = tok.strip()
    if tok.startswith("[") and tok.endswith("]"):
        return [_value(p) for p in _split_flow(tok[1:-1])]
    if tok.startswith("{") and tok.endswith("}"):
        out = {}
        for p in _split_flow(tok[1:-1]):
            if ":" not in p:
                continue
            k, v = _split_kv(p)
            out[_scalar(k)] = _value(v)
        return out
    return None


def _value(tok):
    tok = tok.strip()
    if tok[:1] in "[{":
        flow = _flow(tok)
        if flow is not None:
            return flow
    return _scalar(tok)


def _split_kv(line):
    """Split ``key: value`` at the first colon outside quotes/brackets."""
    depth, quote = 0, None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            if i + 1 >= len(line) or line[i + 1] in " \t":
                return line[:i], line[i + 1:]
    return line, ""


def _strip_comment(line):
    quote, depth = None, 0
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "#" and (i == 0 or line[i - 1] in " \t") and depth == 0:
            return line[:i]
    return line


def _depth(text):
    """Net bracket depth of a fragment, ignoring bracket characters in quotes."""
    depth, quote = 0, None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth


def _prepare(source):
    """Strip comments and blank lines, and fold flow collections that were
    wrapped across several source lines back onto one logical row."""
    rows, pending, indent, depth = [], None, 0, 0
    for raw in source.splitlines():
        line = _strip_comment(raw.rstrip())
        if not line.strip() or line.strip() == "---":
            if pending is None:
                continue
        if pending is not None:
            pending += " " + line.strip()
            depth += _depth(line)
            if depth <= 0:
                rows.append((indent, pending))
                pending, depth = None, 0
            continue
        stripped = line.strip()
        d = _depth(stripped)
        if d > 0:
            indent = len(line) - len(line.lstrip(" "))
            pending, depth = stripped, d
            continue
        rows.append((len(line) - len(line.lstrip(" ")), stripped))
    if pending is not None:
        rows.append((indent, pending))
    return rows


def _parse(source):
    rows = _prepare(source)
    value, _ = _block(rows, 0, 0)
    return value


def _block(rows, i, indent):
    if i >= len(rows):
        return None, i
    if rows[i][1].startswith("- "):
        return _seq(rows, i, rows[i][0])
    if rows[i][1] == "-":
        return _seq(rows, i, rows[i][0])
    return _map(rows, i, rows[i][0])


def _seq(rows, i, indent):
    out = []
    while i < len(rows) and rows[i][0] == indent and (rows[i][1] == "-" or rows[i][1].startswith("- ")):
        body = rows[i][1][2:].strip() if rows[i][1].startswith("- ") else ""
        child_indent = indent + 2
        if not body:
            i += 1
            if i < len(rows) and rows[i][0] > indent:
                val, i = _block(rows, i, rows[i][0])
            else:
                val = None
            out.append(val)
            continue
        if body[:1] in "[{":
            out.append(_value(body)); i += 1; continue
        key, rest = _split_kv(body)
        if rest != "" or body.endswith(":"):
            # inline first key of a mapping element
            synth = [(child_indent, body)]
            j = i + 1
            while j < len(rows) and rows[j][0] > indent:
                synth.append((rows[j][0], rows[j][1])); j += 1
            val, _ = _map(synth, 0, child_indent)
            out.append(val); i = j; continue
        out.append(_value(body)); i += 1
    return out, i


def _map(rows, i, indent):
    out = {}
    while i < len(rows) and rows[i][0] == indent:
        line = rows[i][1]
        if line.startswith("- "):
            break
        key, rest = _split_kv(line)
        key = _scalar(key)
        rest = rest.strip()
        if rest in ("|", ">", "|-", ">-"):
            i += 1
            chunk = []
            while i < len(rows) and rows[i][0] > indent:
                chunk.append(rows[i][1]); i += 1
            out[key] = ("\n" if rest[0] == "|" else " ").join(chunk)
            continue
        if rest:
            out[key] = _value(rest); i += 1; continue
        i += 1
        if i < len(rows) and rows[i][0] > indent:
            val, i = _block(rows, i, rows[i][0])
            out[key] = val
        else:
            out[key] = None
    return out, i
