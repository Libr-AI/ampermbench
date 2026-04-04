from __future__ import annotations

import socket
import socketserver
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import Config


def _docker_bridge_gateway() -> str:
    return (
        subprocess.check_output(
            ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
            text=True,
        )
        .strip()
    )


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


@dataclass(frozen=True)
class RelaySpec:
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        spec: RelaySpec = self.server.relay_spec  # type: ignore[attr-defined]
        upstream = socket.create_connection((spec.target_host, spec.target_port))
        forward = threading.Thread(target=_pipe, args=(self.request, upstream), daemon=True)
        backward = threading.Thread(target=_pipe, args=(upstream, self.request), daemon=True)
        forward.start()
        backward.start()
        forward.join()
        backward.join()
        upstream.close()


class ProxyRelay:
    def __init__(self, spec: RelaySpec) -> None:
        self.spec = spec
        self._server = _ThreadingTCPServer((spec.listen_host, spec.listen_port), _RelayHandler)
        self._server.relay_spec = spec  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _relay_specs(config: Config) -> list[RelaySpec]:
    if not config.docker.proxy.enabled or not config.docker.proxy.add_host_gateway:
        return []
    proxy_urls = [config.docker.proxy.http, config.docker.proxy.https, config.docker.proxy.all]
    gateway = _docker_bridge_gateway()
    specs: dict[tuple[str, int, str, int], RelaySpec] = {}
    for proxy_url in proxy_urls:
        if not proxy_url:
            continue
        parsed = urlparse(proxy_url)
        if parsed.hostname != "host.docker.internal" or not parsed.port:
            continue
        spec = RelaySpec(
            listen_host=gateway,
            listen_port=parsed.port,
            target_host="127.0.0.1",
            target_port=parsed.port,
        )
        specs[(spec.listen_host, spec.listen_port, spec.target_host, spec.target_port)] = spec
    return list(specs.values())


@contextmanager
def proxy_bridge_stack(config: Config):
    with ExitStack() as stack:
        relays: list[ProxyRelay] = []
        for spec in _relay_specs(config):
            relay = ProxyRelay(spec)
            relay.start()
            relays.append(relay)
        try:
            yield
        finally:
            for relay in reversed(relays):
                relay.stop()
