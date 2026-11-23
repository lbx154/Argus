r"""Text measurement and light inline markup.

Supports the small amount of markup real conference figures need:
  ``*italic*``, ``**bold**``, ``_sub_``, ``^sup^`` and ``\n`` line breaks.
  A backslash escapes the next character: ``kernel\_size`` prints literally.

  ``$...$`` is inline maths, set the way LaTeX sets it so a symbol looks the
  same in the figure as in the paragraph that cites it: Latin and lowercase
  Greek letters italic, digits and operators upright, ``_{}``/``^{}`` scripts,
  accents (``\hat``, ``\tilde``, ``\bar``, ``\dot``, ``\vec``),
  ``\mathrm{}``/``\text{}`` upright, ``\mathbf{}`` bold, and the operators
  figures use (``\oplus \otimes \odot \times \cdot \circ \dots ...``).
  ``\$`` prints a literal dollar sign.
Measurement uses the baked Adobe Core-14 advance widths, so a box sized here
is correct in the exported PDF without a font query at run time.
"""

import re
import unicodedata
from ._metrics import WIDTHS

_FALLBACK = 500  # ems/1000 for glyphs outside Latin-1 (CJK, arrows, math)
# Advance widths for the handful of non-Latin-1 glyphs figure labels actually
# use.  Without these the fallback overestimates and labels look gappy.
_EXTRA = {
    "\u00d7": 564, "\u2212": 564, "\u00b1": 564, "\u2264": 549, "\u2265": 549,
    "\u2248": 549, "\u2192": 987, "\u2190": 987, "\u2194": 1042, "\u21d2": 987,
    "\u2295": 768, "\u2297": 768, "\u2299": 768, "\u2211": 713, "\u222b": 274,
    "\u2026": 1000, "\u00b7": 250, "\u2003": 1000, "\u2009": 200, "\u2005": 250,
    "\u2032": 275, "\u2022": 460, "\u2013": 500, "\u2014": 1000,
    "\u2018": 333, "\u2019": 333, "\u201c": 444, "\u201d": 444, "\u00b0": 400,
    "\u03b1": 500, "\u03b2": 500, "\u03b3": 411, "\u03b4": 494, "\u03b5": 439,
    "\u03b8": 521, "\u03bb": 549, "\u03bc": 576, "\u03c0": 549, "\u03c3": 603,
    "\u03c4": 439, "\u03c6": 521, "\u03c8": 686, "\u03c9": 686, "\u0394": 612,
}


def _key(family, bold, italic):
    if family == "helvetica":
        if bold and italic:
            return "helvetica-boldoblique"
        if bold:
            return "helvetica-bold"
        if italic:
            return "helvetica-oblique"
        return "helvetica"
    if bold and italic:
        return "times-bolditalic"
    if bold:
        return "times-bold"
    if italic:
        return "times-italic"
    return "times"


def glyph_width(ch, family="times", bold=False, italic=False):
    table = WIDTHS[_key(family, bold, italic)]
    code = ord(ch)
    if code in table:
        return table[code]
    if ch in _EXTRA:
        return _EXTRA[ch]
    if unicodedata.combining(ch):
        return 0
    base = unicodedata.normalize("NFD", ch)[0]
    if base != ch and ord(base) in table:
        return table[ord(base)]       # precomposed accent: width of its base
    if code > 0x2E80:          # CJK and friends are full-width
        return 1000
    return _FALLBACK


TOKEN_RE = re.compile(r"(\*\*|\*|_\{|\^\{|\}|_|\^)")

# ------------------------------------------------------------------ maths
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω",
    # capitals are upright in LaTeX maths
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ",
    "Psi": "Ψ", "Omega": "Ω",
}
MATH_OPS = {
    "oplus": "⊕", "otimes": "⊗", "odot": "⊙", "ominus": "⊖",
    "times": "×", "cdot": "·", "circ": "∘", "ast": "∗",
    "dots": "…", "ldots": "…", "cdots": "⋯", "pm": "±",
    "in": "∈", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "mapsto": "↦", "sum": "∑", "prod": "∏", "int": "∫",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "approx": "≈", "sim": "∼", "propto": "∝",
    "infty": "∞", "partial": "∂", "nabla": "∇", "mid": "|",
    "parallel": "‖", "|": "‖", "lVert": "‖", "rVert": "‖",
    "{": "{", "}": "}", "langle": "⟨", "rangle": "⟩",
    "prime": "′", "copyright": "©", "cup": "∪", "cap": "∩",
    "forall": "∀", "exists": "∃", "star": "⋆", "dagger": "†",
    ",": " ", ";": " ", ":": " ", "quad": " ", "!": "",
    "left": "", "right": "", "big": "", "Big": "", "qquad": "\u2003\u2003",
}
ACCENTS = {"hat": "̂", "widehat": "̂", "tilde": "̃",
           "widetilde": "̃", "bar": "̄", "overline": "̄",
           "dot": "̇", "ddot": "̈", "vec": "⃗", "check": "̌"}
FONT_CMDS = {"mathrm": (False, False), "text": (False, False),
             "textrm": (False, False), "operatorname": (False, False),
             "mathbf": (True, False), "boldsymbol": (True, True),
             "textbf": (True, False), "mathit": (False, True),
             "mathsf": (False, False), "mathcal": (False, False)}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ",
              "C": "ℂ", "E": "\U0001d53c", "P": "ℙ"}
BINARY = ("oplus", "otimes", "odot", "ominus", "times", "cdot", "circ", "ast", "pm",
          "in", "to", "rightarrow", "leftarrow", "mapsto", "leq", "le", "geq", "ge",
          "neq", "approx", "sim", "propto", "cup", "cap")
FUNCS = ("log", "exp", "max", "min", "arg", "sin", "cos", "tan", "softmax",
         "det", "lim", "sup", "inf", "Pr", "deg", "dim")


def _group(src, j):
    """Read a ``{...}`` group or a single token starting at ``src[j]``.
    Returns (content, next_index)."""
    while j < len(src) and src[j] == " ":
        j += 1
    if j >= len(src):
        return "", j
    if src[j] == "{":
        depth, k = 1, j + 1
        while k < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[k], 0)
            k += 1
        return src[j + 1:k - 1], k
    if src[j] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", src[j:])
        return m.group(0), j + len(m.group(0))
    return src[j], j + 1


def math_atoms(src, bold=False, italic=None, script=0):
    """Typeset a maths string into atoms ``(chars, bold, italic, script, kind)``.

    ``kind`` is ``var`` (a letter: a symbol a reader must be told about),
    ``op`` (an operator), ``word`` (an upright multi-letter name), or
    ``plain`` (digits, punctuation, space).  Nested scripts flatten to one
    level, which is all a figure label ever needs.
    """
    out = []
    j = 0
    while j < len(src):
        c = src[j]
        if c == " ":
            j += 1
            continue
        if c in "_^":
            body, j = _group(src, j + 1)
            lvl = -1 if c == "_" else 1
            for ch, b, it, _, kind in math_atoms(body, bold, italic, lvl):
                out.append((ch, b, it, lvl if script == 0 else script, kind))
            continue
        if c == "'":
            out.append(("′", bold, False, 1 if script == 0 else script, "plain"))
            j += 1
            continue
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", src[j:])
            name = m.group(1)
            j += len(m.group(0))
            if name in ACCENTS:
                body, j = _group(src, j)
                inner = math_atoms(body, bold, italic, script)
                if inner:
                    ch, b, it, sc, kind = inner[0]
                    inner[0] = (unicodedata.normalize("NFC", ch + ACCENTS[name]),
                                b, it, sc, kind)
                out.extend(inner)
            elif name in FONT_CMDS:
                body, j = _group(src, j)
                fb, fi = FONT_CMDS[name]
                word = body.replace("\\ ", " ")
                if name in ("mathbf", "boldsymbol", "mathit"):
                    for ch, b, it, sc, kind in math_atoms(body, fb or bold, fi, script):
                        out.append((ch, b, it, sc, kind))
                else:
                    kind = "word" if len(word.strip()) > 1 else "var"
                    out.append((word, fb or bold, fi, script, kind))
            elif name == "mathbb":
                body, j = _group(src, j)
                out.append(("".join(BLACKBOARD.get(ch, ch) for ch in body),
                            bold, False, script, "var"))
            elif name in GREEK:
                g = GREEK[name]
                out.append((g, bold, (italic if italic is not None else g.islower()),
                            script, "var"))
            elif name in MATH_OPS:
                if MATH_OPS[name]:
                    glyph = MATH_OPS[name]
                    if name in BINARY and script == 0:
                        # no left operand -> ordinary symbol, as in TeX
                        glyph = (" " + glyph + " ") if out else glyph
                    out.append((glyph, bold, False, script,
                                "plain" if name in (",", ";", ":", "quad", "{", "}",
                                                    "langle", "rangle", "lVert",
                                                    "rVert", "|", "mid") else "op"))
            elif name in FUNCS:
                # an operator name is followed by a thin space, as in TeX
                nxt = src[j:].lstrip()[:1]
                out.append((name + (" " if nxt and nxt not in "(_^" else ""),
                            bold, False, script, "word"))
            else:
                out.append((name, bold, False, script, "word"))
            continue
        if c.isalpha():
            out.append((c, bold, True if italic is None else italic, script, "var"))
        elif c in "+-=<>":
            g = {"-": "\u2212"}.get(c, c)
            # TeX spaces binary operators and relations; a leading minus, or
            # one after another operator or an opening bracket, is unary
            unary = c in "+-" and (not out or out[-1][4] == "op"
                                   or out[-1][0].strip()[-1:] in "([{,;")
            if script == 0 and not unary and out:
                g = " " + g + " "
            out.append((g, bold, False, script, "op"))
        elif c in "{}":
            pass
        else:
            out.append((c, bold, False, script, "plain"))
        j += 1
    return out


def parse_runs(text, bold=False, italic=False):
    r"""Split inline markup into styled runs.

    Returns a list of ``(chars, bold, italic, script)`` where ``script`` is
    ``0`` for baseline, ``-1`` subscript, ``+1`` superscript.

    A backslash escapes the next character, so ``kernel\_size`` is a literal
    underscore rather than a subscript.  Code-derived labels are full of
    identifiers and need this; so does any spec that wants to print an
    asterisk.  ``$...$`` switches to maths (see ``math_atoms``).
    """
    runs = []
    for r in parse_atoms(text, bold, italic):
        if runs and runs[-1][1:4] == r[1:4]:
            runs[-1] = (runs[-1][0] + r[0],) + runs[-1][1:4]
        else:
            runs.append(r[:4])
    return runs or [("", bold, italic, 0)]


def parse_atoms(text, bold=False, italic=False):
    """Like ``parse_runs`` but each run also carries its kind: ``text`` for
    ordinary label text, or the ``math_atoms`` kind for maths."""
    runs, buf = [], []
    b, i, script = bold, italic, 0
    pending_close = []
    j = 0
    text = str(text)

    def flush():
        if buf:
            runs.append(("".join(buf), b, i, script, "text"))
            del buf[:]
    while j < len(text):
        if text[j] == "\\" and j + 1 < len(text):
            buf.append(text[j + 1]); j += 2; continue
        if text[j] == "$":
            k = j + 1
            while k < len(text) and not (text[k] == "$" and text[k - 1] != "\\"):
                k += 1
            if k < len(text):
                flush()
                for atom in math_atoms(text[j + 1:k], b):
                    runs.append(atom)
                j = k + 1
                continue
        if text.startswith("**", j):
            flush(); b = not b; j += 2; continue
        if text[j] == "*":
            flush(); i = not i; j += 1; continue
        if text.startswith("_{", j) or text.startswith("^{", j):
            flush(); script = -1 if text[j] == "_" else 1
            pending_close.append("brace"); j += 2; continue
        if text[j] == "}" and pending_close:
            flush(); pending_close.pop(); script = 0; j += 1; continue
        if text[j] in "_^" and j + 1 < len(text):
            flush(); script = -1 if text[j] == "_" else 1
            buf.append(text[j + 1]); flush(); script = 0; j += 2; continue
        buf.append(text[j]); j += 1
    flush()
    # merge neighbours with identical style so the SVG and PPTX stay compact
    merged = []
    for r in runs:
        r = (unicodedata.normalize("NFC", r[0]),) + tuple(r[1:])
        if merged and merged[-1][1:4] == r[1:4] and merged[-1][4] == r[4] == "text":
            merged[-1] = (merged[-1][0] + r[0],) + merged[-1][1:]
        else:
            merged.append(r)
    return merged


def plain(text):
    """The label as a reader sees it: markup removed, maths typeset."""
    return "".join(r[0] for r in parse_atoms(text))


# Operator glyphs that mean something a reader has to be told.  An operator
# node (``shape: op``) contributes its glyph to the same list.
OPERATOR_GLYPHS = "⊕⊗⊙⊖©Ⓒ∘σΣ∑∏‖"
_REPEAT = re.compile(r"\u00d7\s*([A-Za-z])(?![A-Za-z])")
_BRACKET_STACK = re.compile(r"\[[^\[\]]*;[^\[\]]*\]")
_TEXT_TOKEN = re.compile(r"[^\W\d_]+[̀-ͯ]*$", re.UNICODE)


def symbols(text):
    """The symbols a reader has to have defined, in reading order.

    Returns a list of dicts ``{key, shown, sub, sup, upright}``:

    * ``key`` -- the base glyph(s) the symbol is filed under, accents kept, so
      ``z``, ``ẑ`` (z-hat) and ``c̃`` (c-tilde) are three symbols and
      ``z_{1:4}`` / ``z_4`` both file under ``z``;
    * ``upright`` -- True when a variable is set upright (old ``z_t`` markup
      outside ``$...$``), which disagrees with the italic $z_t$ in the text.

    Sources: maths atoms of kind ``var``; a word immediately followed by a
    sub/superscript outside maths (only its last letter run is the symbol);
    a single italic letter; operator glyphs; and ``[a; b]`` stacking.
    """
    atoms = parse_atoms(text)
    out = []

    def add(key, shown, upright=False, sub=None, sup=None):
        key = unicodedata.normalize("NFC", key)
        out.append({"key": key, "shown": shown, "upright": upright,
                    "sub": sub, "sup": sup})

    k = 0
    while k < len(atoms):
        chars, b, it, sc, kind = atoms[k]
        nxt = atoms[k + 1] if k + 1 < len(atoms) else None
        if kind == "var" and sc == 0:
            sub = sup = None
            m = k + 1
            while m < len(atoms) and atoms[m][3] != 0:
                if atoms[m][3] < 0:
                    sub = (sub or "") + atoms[m][0]
                else:
                    sup = (sup or "") + atoms[m][0]
                m += 1
            shown = chars + ("_" + sub if sub else "") + ("^" + sup if sup else "")
            add(chars, shown, not it, sub, sup)
            k = m
            continue
        if kind == "word" and nxt is not None and nxt[3] != 0:
            # \mathrm{mean}_t -- a named quantity
            sub = ""
            for a in atoms[k + 1:]:
                if a[3] == 0:
                    break
                sub += a[0] if a[3] < 0 else ""
            sub = sub or None
            add(chars, chars + ("_" + sub if sub else ""), False, sub)
        if kind == "text" and sc == 0 and nxt is not None and nxt[3] != 0 \
                and nxt[4] == "text":
            m = _TEXT_TOKEN.search(chars.rstrip())
            if m and chars.rstrip() == chars and len(m.group(0)) <= 3:
                tok = m.group(0)
                add(tok, tok + ("_" if nxt[3] < 0 else "^") + nxt[0],
                    not it, nxt[0] if nxt[3] < 0 else None,
                    nxt[0] if nxt[3] > 0 else None)
        if kind == "text" and it and sc == 0 and len(chars.strip()) == 1 \
                and chars.strip().isalpha():
            add(chars.strip(), chars.strip())
        if kind in ("text", "op"):
            for ch in chars:
                if ch in OPERATOR_GLYPHS:
                    add(ch, ch)
        k += 1
    for m in _BRACKET_STACK.finditer(plain(text)):
        add("[;]", m.group(0))
    # repeat badges written as plain text, "\u00d7L" / "x N": L and N are symbols
    if not any(a[4] != "text" for a in atoms):
        for m in _REPEAT.finditer(plain(text)):
            add(m.group(1), "\u00d7" + m.group(1))
    return out


SCRIPT_SCALE = 0.72
SCRIPT_RISE = 0.32
SCRIPT_DROP = 0.16


def line_width(line, size, family="times", bold=False, italic=False):
    total = 0.0
    for chars, b, i, script in parse_runs(line, bold, italic):
        s = size * (SCRIPT_SCALE if script else 1.0)
        for ch in chars:
            total += glyph_width(ch, family, b, i) * s / 1000.0
    return total


def measure(text, size, family="times", bold=False, italic=False, leading=1.18):
    """Return ``(width, height, lines)`` in points for a multi-line label."""
    lines = str(text).split("\n") if text else [""]
    w = max((line_width(ln, size, family, bold, italic) for ln in lines), default=0.0)
    h = size * leading * len(lines) if len(lines) > 1 else size
    return w, h, lines


def wrap(text, max_width, size, family="times", bold=False, italic=False):
    """Greedy word wrap, honouring explicit newlines already in ``text``."""
    out = []
    for para in str(text).split("\n"):
        words, cur = para.split(), ""
        for word in words:
            trial = (cur + " " + word).strip()
            if cur and line_width(trial, size, family, bold, italic) > max_width:
                out.append(cur)
                cur = word
            else:
                cur = trial
        out.append(cur)
    return "\n".join(out)
