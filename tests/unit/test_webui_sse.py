"""Unit tests for WebUI SSE formatting."""

from __future__ import annotations

import json

from ui.web.webcore import WebUIApp


def test_format_sse_includes_event_and_payload() -> None:
    payload = {"status": {"ok": True}}
    rendered = WebUIApp._format_sse(payload, event="overview")

    assert rendered.startswith("event: overview\n")
    assert rendered.endswith("\n\n")
    assert "data: " in rendered
    data_line = rendered.split("data: ", 1)[1].strip()
    parsed = json.loads(data_line)
    assert parsed == payload
