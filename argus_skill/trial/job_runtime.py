"""Start the public HTTPS tunnel inside a network-disabled compute container."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .socket_forward import start_forward


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("A compute command is required")
    forward = None
    if Path("/egress/egress.sock").is_socket():
        forward = start_forward(3128, "/egress/egress.sock")
        os.environ.update({
            "HTTPS_PROXY": "http://127.0.0.1:3128",
            "HTTP_PROXY": "http://127.0.0.1:3128",
            "https_proxy": "http://127.0.0.1:3128",
            "http_proxy": "http://127.0.0.1:3128",
            "NO_PROXY": "localhost,127.0.0.1,::1",
        })
    try:
        return subprocess.call(sys.argv[1:])
    finally:
        if forward is not None:
            forward.terminate()
            forward.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
