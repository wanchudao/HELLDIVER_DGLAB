"""Plugin regressions stay offline, including when collected separately."""
import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_connections(monkeypatch):
    def blocked(*args, **kwargs):
        raise OSError("Network/device access is disabled in plugin tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
