"""End-to-end tests for two-port architecture and path routing."""

from __future__ import annotations

import http.client
import threading
from wsgiref.simple_server import make_server

from werkzeug.test import Client
from werkzeug.wrappers import Response

from hosting.dispatcher import HostingDispatcher


def _simple_app(label: str):
    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [label.encode("utf-8")]

    return app


def _serve_wsgi(app):
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_dispatcher_routes_dav_and_api():
    dispatcher = HostingDispatcher(service=object())
    dispatcher.set_webdav_app(_simple_app("dav"))
    dispatcher.set_api_app(_simple_app("api"))

    client = Client(dispatcher.get_wsgi_app(), Response)
    resp = client.get("/dav/anything")
    assert resp.status_code == 200
    assert resp.data == b"dav"

    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.data == b"api"


def test_dispatcher_serves_via_localhost_only():
    dispatcher = HostingDispatcher(service=object())
    dispatcher.set_webdav_app(_simple_app("dav"))
    dispatcher.set_api_app(_simple_app("api"))

    server, thread = _serve_wsgi(dispatcher.get_wsgi_app())
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        conn.request("GET", "/dav/ping")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert body == b"dav"
    finally:
        server.shutdown()
        thread.join(timeout=2)
