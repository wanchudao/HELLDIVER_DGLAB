"""All automated tests are offline. Hardware scripts require a separate opt-in."""
import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_connections(monkeypatch):
    def blocked(*args, **kwargs):
        raise OSError("Network/device access is disabled in automated tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
