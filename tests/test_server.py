import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from conftest import ocean_source, spot_source
from surfslop import ai, config, fusion, netguard, server, spots


@pytest.fixture
def portal(monkeypatch):
    """The real server on a free port, with canned sources instead of the internet."""
    def forecast(spot, cfg=None, refresh=False, public=False):
        assert not public                                    # the private portal never builds the public edition
        data = fusion.fuse(spot, [spot_source("surfforecast", 3, 5), ocean_source("nws", 6, 14, 300)], tides=[], stored={})
        data.update(tide_station="Santa Cruz", buoys=[], off=["surfline"], now=data["hours"][12], public=False)
        data["writeups"] = [{"source": "surf-forecast.com", "day": None, "text": "their words"}]
        return data

    monkeypatch.setattr(fusion, "forecast", forecast)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def fetch(url, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_page_and_guide(portal):
    status, headers, body = fetch(portal + "/")
    assert status == 200 and b"surfslop" in body and "default-src 'self'" in headers["Content-Security-Policy"]
    assert fetch(portal + "/static/app.js")[0] == 200 and fetch(portal + "/static/../config.py")[0] == 404
    status, headers, body = fetch(portal + "/llms.txt")
    assert status == 200 and headers["Content-Type"].startswith("text/markdown") and b"/v1/forecast/{spot}" in body


def test_spots_and_forecast(portal):
    listed = json.loads(fetch(portal + "/v1/spots?q=ocean")[2])["spots"]
    assert listed[0]["id"] == "ocean-beach" and len(json.loads(fetch(portal + "/v1/spots")[2])["spots"]) == len(spots.SPOTS)
    status, _, body = fetch(portal + "/v1/forecast/OB?hours=0")
    data = json.loads(body)
    assert status == 200 and data["spot"]["id"] == "ocean-beach" and "hours" not in data and data["off"] == ["surfline"]
    assert "writeups" not in data and b"their words" not in body      # the sources' own words stay on the Mac
    assert set(data["days"][0]["by_source"]) == {"surfforecast", "nws"} and data["overview"] is None
    md = fetch(portal + "/v1/forecast/steamer-lane?format=md")[2].decode()
    assert md.startswith("# Steamer Lane surf forecast") and "Surfline is switched off" in md
    status, _, body = fetch(portal + "/v1/forecast/atlantis")
    assert status == 404 and json.loads(body)["error"]["code"] == "unknown_spot"


def test_overview_and_card(portal):
    data = json.loads(fetch(portal + "/v1/overview?days=2")[2])
    assert [r["spot"]["name"] for r in data["spots"]][:2] == ["Steamer Lane", "Pleasure Point"] and len(data["spots"][0]["days"]) == 2
    assert "| Steamer Lane |" in fetch(portal + "/v1/overview?format=md")[2].decode()
    status, headers, body = fetch(portal + "/v1/card.png")
    assert status == 200 and headers["Content-Type"] == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"


def test_ai_stream_reports_when_the_model_is_off(portal, monkeypatch):
    cfg = config.load()
    cfg.llm_url = ""
    config.save(cfg)
    body = fetch(portal + "/v1/forecast/steamer-lane/ai")[2].decode()
    assert "event: unavailable" in body and "switched off" in body
    monkeypatch.setattr(ai, "stream", lambda *a, **k: iter(["**Bottom line:** ", "go."]))
    body = fetch(portal + "/v1/forecast/steamer-lane/ai")[2].decode()
    assert body.count("event: text") == 2 and body.rstrip().endswith("data: {}") and "event: done" in body


def test_only_local_callers_and_hosts(portal):
    status, _, body = fetch(portal + "/v1/spots", {"Host": "evil.example.com"})
    assert status == 403 and "Host" in json.loads(body)["error"]["message"]
    assert fetch(portal + "/v1/spots", {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert fetch(portal + "/", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})[0] == 200   # a link still opens
    assert netguard.allowed("8.8.8.8", "localhost", []) and netguard.allowed("100.101.102.104", "100.101.102.103:7873", []) is None
    assert netguard.allowed("192.168.1.7", "macbook.local:7873", []) is None
    refused = netguard.allowed("100.101.102.105", "100.101.102.103:7873", ["100.101.102.104"])
    assert "surfslop peers 100.101.102.105" in refused
    assert netguard.allowed("100.101.102.104", "100.101.102.103:7873", ["100.101.102.104"]) is None
    assert netguard.allowed("127.0.0.1", "localhost:7873", ["100.101.102.104"]) is None
    assert netguard.allowed("100.0.0.1", "localhost", []) is not None     # 100.0/10 is public; only 100.64/10 is Tailscale


def test_requests_with_a_body_are_refused_and_the_connection_closed(portal):
    request = urllib.request.Request(portal + "/v1/spots", data=b"GET /v1/status HTTP/1.1\r\n\r\n", method="GET")
    with pytest.raises(urllib.error.HTTPError) as smuggled:
        urllib.request.urlopen(request, timeout=10)
    assert smuggled.value.code == 400 and smuggled.value.headers["Connection"] == "close"
    with pytest.raises(urllib.error.HTTPError) as posted:
        urllib.request.urlopen(urllib.request.Request(portal + "/v1/spots", data=b"x=1", method="POST"), timeout=10)
    assert posted.value.code == 405 and posted.value.headers["Allow"] == "GET, HEAD" and posted.value.headers["Connection"] == "close"
    status, headers, _ = fetch(portal + "/v1/spots")
    assert status == 200 and headers["Cross-Origin-Resource-Policy"] == "same-origin" and "Strict-Transport-Security" not in headers


def test_home_screen_icon_and_manifest(portal):
    page = fetch(portal + "/")[2].decode()
    assert 'rel="apple-touch-icon" href="/static/apple-touch-icon.png"' in page and 'rel="manifest"' in page
    for path in ("/static/apple-touch-icon.png", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/static/icon-512.png", "/favicon.ico"):
        status, headers, body = fetch(portal + path)
        assert status == 200 and headers["Content-Type"] == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n", path
    status, headers, body = fetch(portal + "/static/manifest.webmanifest")
    manifest = json.loads(body)
    assert status == 200 and headers["Content-Type"].startswith("application/manifest+json")
    assert manifest["display"] == "standalone" and {i["sizes"] for i in manifest["icons"]} == {"192x192", "512x512"}


def test_a_reader_that_hangs_up_leaves_no_traceback(capfd):
    import socket
    import struct
    import time

    httpd = server.QuietServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        for _ in range(3):                                    # as a phone does when a page closes or it sleeps
            sock = socket.create_connection(httpd.server_address)
            sock.sendall(b"GET /v1/spots HTTP/1.1\r\nHost: localhost\r\n\r\n")
            assert sock.recv(64).startswith(b"HTTP/1.1 200")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # close with a reset
            sock.close()
        time.sleep(0.5)
        assert fetch(f"http://127.0.0.1:{httpd.server_address[1]}/v1/spots")[0] == 200       # still serving
    finally:
        httpd.shutdown()
    assert "Traceback" not in capfd.readouterr().err
