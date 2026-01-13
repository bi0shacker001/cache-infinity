"""Unit tests for server signal handling."""

from __future__ import annotations

import pytest

import core.server as server_module


def test_shutdown_signal_handler_invokes_callback(monkeypatch):
    recorded = {}

    def fake_signal(sig, handler):
        recorded[sig] = handler

    monkeypatch.setattr(server_module.signal, "signal", fake_signal)

    calls = []

    def callback(reason: str) -> None:
        calls.append(reason)

    server_module._install_shutdown_signal(callback)
    sigint = getattr(server_module.signal, "SIGINT", None)
    if sigint is None:
        pytest.skip("SIGINT not supported on this platform")

    handler = recorded.get(sigint)
    assert handler is not None

    handler(sigint, None)

    assert calls == [server_module.signal.Signals(sigint).name]
