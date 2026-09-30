"""mDNS discovery of boards advertising _spookyeyes._tcp."""
from __future__ import annotations

import socket
import time

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

SERVICE = "_spookyeyes._tcp.local."


class _Collector(ServiceListener):
    def __init__(self):
        self.found: dict[str, tuple[str, int]] = {}

    def _record(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name, timeout=2000)
        if not info or not info.addresses:
            return
        self.found[name] = (socket.inet_ntoa(info.addresses[0]), info.port or 80)

    def add_service(self, zc, type_, name):
        self._record(zc, type_, name)

    def update_service(self, zc, type_, name):
        self._record(zc, type_, name)

    def remove_service(self, zc, type_, name):
        pass


def browse(seconds: float = 4.0) -> list[tuple[str, int]]:
    """Returns (host, port) for every board seen within the window."""
    zc = Zeroconf()
    collector = _Collector()
    try:
        ServiceBrowser(zc, SERVICE, collector)
        time.sleep(seconds)
    finally:
        zc.close()
    return sorted(set(collector.found.values()))


def scan(seconds: float = 4.0):
    """Browse and register every board found. Returns (devices, errors)."""
    from . import client, services

    devices, errors = [], []
    for host, port in browse(seconds):
        try:
            devices.append(services.register(host, port))
        except client.DeviceError as err:
            errors.append(str(err))
    return devices, errors
