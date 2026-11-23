#!/usr/bin/env python3
"""Draft a figure spec from source code.

    python3 scripts/from_code.py path/to/models/            # PyTorch source
    python3 scripts/from_code.py configs/model.py --mm      # mmdet-style config
    python3 scripts/from_code.py src/ --model MyNet -o spec.yaml

Nothing is imported or executed -- both readers parse with ``ast``.  The
output is a first draft: it gets the modules, the order and the branch
structure right, and it is expected that you then rename boxes, drop what the
paper does not discuss, and attach real input/output images.  Pipe it into
render.py once you are happy:

    python3 scripts/from_code.py src/ -o fig.yaml && \
    python3 scripts/render.py fig.yaml -o build/fig -f svg,pdf,pptx
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from cvprfig.code2fig import torchscan, mmconfig, emit   # noqa: E402
from cvprfig import specdump                              # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="+", help="file(s) or directory to read")
    ap.add_argument("--mm", action="store_true",
                    help="treat the source as an mmengine/mmdet `model = dict(...)` config")
    ap.add_argument("--model", help="class name to draw (default: the top of the tree)")
    ap.add_argument("-o", "--out", help="write the spec here (default: stdout)")
    ap.add_argument("--venue", default="cvpr")
    ap.add_argument("--width", default="double", help="single | double | points")
    ap.add_argument("--max-nodes", type=int, default=12,
                    help="corpus median framework figure has 11 boxes")
    ap.add_argument("--stages", help="comma-separated panel titles, left to right")
    ap.add_argument("--depth", type=int, default=1, help="config nesting depth (--mm)")
    ap.add_argument("--keep-loss", action="store_true")
    ap.add_argument("--no-detail", action="store_true",
                    help="omit channel-count captions")
    ap.add_argument("--list", action="store_true", help="list candidate classes and exit")
    a = ap.parse_args(argv)

    if a.mm:
        g, name = mmconfig.build(a.source[0], max_nodes=a.max_nodes, depth=a.depth,
                                 keep_loss=a.keep_loss)
        found = [name]
    else:
        if a.list:
            defs = torchscan.collect(a.source)
            for k in sorted(defs):
                print("%-40s %2d submodules  %s" % (
                    k, len(defs[k].submodules),
                    "forward" if defs[k].forward else "-"))
            return 0
        g, name, found = torchscan.build(
            a.source, model=a.model, max_nodes=a.max_nodes, keep_loss=a.keep_loss)

    spec, note = emit.fit(
        g, venue=a.venue, width=a.width, detail=not a.no_detail,
        stages=[s.strip() for s in a.stages.split(",")] if a.stages else None,
        title=name)
    text = specdump.dump(spec)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s" % a.out, file=sys.stderr)
    else:
        sys.stdout.write(text)
    print("  %s from %s" % (g.summary(), name), file=sys.stderr)
    if note:
        print("  fit: %s" % note, file=sys.stderr)
    if not a.mm and len(found) > 1:
        print("  (%d model classes seen; --list to choose another)" % len(found),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
