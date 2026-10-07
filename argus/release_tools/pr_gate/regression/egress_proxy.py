"""Allow HTTPS only to the Copilot service; expose no host port."""

from __future__ import annotations

import ipaddress
import select
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOWED_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "api.githubcopilot.com",
    "api.individual.githubcopilot.com",
    "api.business.githubcopilot.com",
    "api.enterprise.githubcopilot.com",
    "copilot-proxy.githubusercontent.com",
})


def allowed_destination(authority: str) -> str | None:
    host, separator, port = authority.rpartition(":")
    if separator and port == "443" and host.lower() in ALLOWED_HOSTS:
        return host.lower()
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        # Do not persist request headers, paths, or credentials.
        return

    def do_CONNECT(self) -> None:
        host = allowed_destination(self.path)
        if host is None:
            self.send_error(403, "Destination not allowed")
            return
        remote = None
        try:
            for entry in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM):
                address = entry[4]
                if not ipaddress.ip_address(address[0]).is_global:
                    continue
                try:
                    remote = socket.create_connection(address, timeout=20)
                    break
                except OSError:
                    continue
        except OSError:
            self.send_error(502, "DNS unavailable")
            return
        if remote is None:
            self.send_error(502, "Service unavailable")
            return
        with remote:
            self.send_response(200, "Connection established")
            self.end_headers()
            self.wfile.flush()
            peers = (self.connection, remote)
            try:
                while True:
                    readable, _, _ = select.select(peers, [], [], 180)
                    if not readable:
                        return
                    for source in readable:
                        payload = source.recv(65536)
                        if not payload:
                            return
                        destination = remote if source is self.connection else self.connection
                        destination.sendall(payload)
            except (OSError, TimeoutError):
                return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 3128), Handler).serve_forever()
