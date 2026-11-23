"""cvprfig -- a deterministic layout engine for CVPR/ICCV/NeurIPS-style
pipeline, framework and teaser figures.

Pure standard library.  Reads a declarative spec (YAML or JSON), solves the
layout in final rendered points, and writes editable vector output: SVG, PDF,
PNG, EMF, native Visio (.vsdx) and native PowerPoint (.pptx).
"""

__version__ = "3.1.0"

from .figure import Figure          # noqa: F401
from .miniyaml import load, load_path  # noqa: F401


def build(spec, base=None):
    return Figure(spec, base=base)
