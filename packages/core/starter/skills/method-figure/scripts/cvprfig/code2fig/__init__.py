"""Architecture figures from source code.

Two front-ends, one intermediate graph, one emitter:

    torchscan.build(paths)   nn.Module source  -> Graph
    mmconfig.build(path)     mmdet-style config -> Graph
    emit.emit(graph)         Graph              -> figure spec dict

Neither front-end imports or executes the code it reads.
"""

from . import graph, torchscan, mmconfig, emit   # noqa: F401
from .graph import Graph, Node                    # noqa: F401
from .emit import emit as to_spec                 # noqa: F401
