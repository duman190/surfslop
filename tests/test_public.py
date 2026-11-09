import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from conftest import ocean_source, spot_source
from surfslop import ai, config, fusion, netguard, server
from surfslop import stats as statistics
from surfslop.sources import nws, openmeteo, surfforecast, surfline
from surfslop.spots import BY_ID

HOST = {"Host": "surf.example.com"}


@pytest.fixture
def public(monkeypatch, tmp_path):
    """The public portal on a free port, as the tunnel would reach it, with canned sources."""
    seen = []

    def forecast(spot, cfg=None, refresh=False, public=False):
        seen.append((spot.id, refresh, public))
        sources = [ocean_source("nws", 6, 14, 300)] + ([] if public else [spot_source("surfline", 4, 6, note="Private words.")])
        data = fusion.fuse(spot, sources, tides=[], stored={})
        data.update(tide_station="Santa Cruz", buoys=[], off=[] if public else ["surfforecast"], now=data["hours"][12], public=public)
        return data

    monkeypatch.setattr(fusion, "forecast", forecast)
    stats = statistics.Stats(tmp_path / "stats.json")
    portal = server.Portal(public=True, hosts=("surf.example.com",), stats=stats)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("PublicHandler", (server.Handler,), {"portal": portal}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", portal, seen
    httpd.shutdown()


def fetch(url, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={**HOST, **(headers or {})}), timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_public_fusion_uses_the_open_sources_only(monkeypatch):
    asked = []
    for module in (surfline, surfforecast, nws, openmeteo):
        monkeypatch.setattr(module, "fetch", lambda spot, name=module.__name__.rsplit(".", 1)[-1]: asked.append(name) or [])
    cfg = config.load()
    cfg.disabled = []
    fusion.gather(BY_ID["steamer-lane"], cfg, public=True)
    assert sorted(asked) == ["nws", "openmeteo"]
    asked.clear()
    fusion.gather(BY_ID["steamer-lane"], cfg)
    assert sorted(asked) == ["nws", "openmeteo", "surfforecast", "surfline"]


def test_public_answers_carry_no_private_source(public):
    base, _, seen = public
    status, headers, body = fetch(base + "/v1/forecast/steamer-lane?refresh=1")
    data = json.loads(body)
    assert status == 200 and headers["Strict-Transport-Security"] == "max-age=31536000"
    assert seen == [("steamer-lane", False, True)]                      # the public edition, and refresh=1 did nothing
    assert [s["id"] for s in data["sources"]] == ["nws"] and data["off"] == [] and "writeups" not in data
    assert b"urfline" not in body and b"Private words" not in body and b"surf-forecast" not in body
    assert b"urfline" not in fetch(base + "/v1/forecast/steamer-lane?format=md")[2]
    assert b"urfline" not in fetch(base + "/v1/overview?format=md")[2]
    status, _, body = fetch(base + "/v1/status")
    assert status == 200 and set(json.loads(body)) == {"version", "generated", "spots", "sources"} and b"100.120" not in body


def test_public_host_must_be_yours_and_the_peer_this_mac(public):
    base, _, _ = public
    status, _, body = fetch(base + "/v1/spots", {"Host": "evil.example.com"})
    assert status == 403 and json.loads(body)["error"]["message"] == "not available here"    # says nothing about this Mac
    assert fetch(base + "/v1/spots", {"Host": "localhost:7874"})[0] == 200
    assert netguard.public_allowed("100.101.102.105", "surf.example.com", ["surf.example.com"])  # not from this Mac: refused
    assert netguard.public_allowed("192.168.1.9", "localhost", []) and netguard.public_allowed("127.0.0.1", "surf.example.com", []) 
    assert netguard.public_allowed("127.0.0.1", "SURF.example.com:443", ["surf.example.com"]) is None


def test_visitor_is_cloudflares_address_only_from_loopback():
    assert netguard.visitor("127.0.0.1", "203.0.113.9") == "203.0.113.9"
    assert netguard.visitor("10.0.0.5", "203.0.113.9") == "10.0.0.5"          # a LAN caller can't claim to be someone else
    assert netguard.visitor("127.0.0.1", None) == "127.0.0.1" and netguard.visitor("127.0.0.1", "not an ip") == "127.0.0.1"
    assert netguard.visitor("::1", "2001:db8:1:2:aaaa::1") == netguard.visitor("::1", "2001:db8:1:2:bbbb::2") == "2001:db8:1:2::/64"


def test_each_visitor_is_rate_limited_on_their_own(public, monkeypatch):
    base, portal, _ = public
    monkeypatch.setattr(server.Limiter, "BURST", 4)
    alice, bob = {"CF-Connecting-IP": "203.0.113.9"}, {"CF-Connecting-IP": "198.51.100.7"}
    assert [fetch(base + "/v1/spots", alice)[0] for _ in range(6)] == [200, 200, 200, 200, 429, 429]
    status, headers, _ = fetch(base + "/v1/spots", alice)
    assert status == 429 and headers["Retry-After"] == "5"
    assert fetch(base + "/v1/spots", bob)[0] == 200                           # someone else is unaffected
    limiter = server.Limiter()
    assert limiter.allow("x", cost=3) and limiter.allow("x") and not limiter.allow("x")   # a picture costs three


def test_visitors_cannot_make_the_model_write_at_will(public, monkeypatch):
    base, portal, _ = public
    monkeypatch.setattr(ai, "stream", lambda *a, **k: iter(["**Bottom line:** ", "go."]))
    monkeypatch.setattr(ai, "spot_cached", lambda data, cfg=None: None)
    data = json.loads(fetch(base + "/v1/forecast/steamer-lane?ai=1&hours=0")[2])
    assert data["overview"] is None                                           # ai=1 is ignored in public
    body = fetch(base + "/v1/forecast/steamer-lane/ai")[2].decode()
    assert body.count("event: text") == 2 and "event: done" in body          # within the ration: written
    monkeypatch.setattr(server.Limiter, "WRITES_AN_HOUR", 1)
    body = fetch(base + "/v1/forecast/steamer-lane/ai")[2].decode()
    assert "event: unavailable" in body and "resting" in body                # the hour's ration is spent
    monkeypatch.setattr(ai, "spot_cached", lambda data, cfg=None: {"text": "written this morning", "generated": 1})
    body = fetch(base + "/v1/forecast/steamer-lane/ai")[2].decode()
    assert "written this morning" in body and "event: done" in body          # one already written is always free
    limiter = server.Limiter()
    assert limiter.stream_open("a") and limiter.stream_open("a") and not limiter.stream_open("a")   # two at a time each
    limiter.stream_closed("a")
    assert limiter.stream_open("a")
    assert portal.stats.summary()["month"]["ai"] == {"written": 1, "cached": 1, "mean_seconds": portal.stats.summary()["month"]["ai"]["mean_seconds"]}


def test_stats_count_visitors_without_keeping_addresses(public, tmp_path):
    base, portal, _ = public
    for ip in ("203.0.113.9", "203.0.113.9", "198.51.100.7"):
        fetch(base + "/v1/forecast/ocean-beach?hours=0", {"CF-Connecting-IP": ip})
    fetch(base + "/v1/forecast/atlantis", {"CF-Connecting-IP": "203.0.113.9"})
    fetch(base + "/v1/spots", {"Host": "evil.example.com"})
    summary = portal.stats.summary()
    assert summary["today"]["requests"] == 5 and summary["today"]["visitors"] == 2 and summary["month"]["visitors"] == 2
    assert summary["month"]["spots"] == {"ocean-beach": 3} and summary["month"]["refused"] == {"unknown_spot": 1, "forbidden": 1}
    assert summary["today"]["ms"]["forecast"]["count"] == 3 and sum(summary["minutes"]) == 5
    portal.stats.flush()
    saved = (tmp_path / "stats.json").read_text()
    assert "203.0.113.9" not in saved and "198.51.100.7" not in saved and oct((tmp_path / "stats.json").stat().st_mode & 0o777) == "0o600"
    again = statistics.Stats(tmp_path / "stats.json")                           # survives a restart
    assert again.summary()["month"]["visitors"] == 2 and again.summary()["today"]["requests"] == 5


def test_stats_page_is_for_the_tailnet_never_the_tunnel(tmp_path):
    stats = statistics.Stats(tmp_path / "stats.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), statistics.make_handler(stats))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        local = {"Host": "localhost"}
        status, _, body = fetch(base + "/stats.json", local)
        assert status == 200 and json.loads(body)["today"]["requests"] == 0
        assert fetch(base + "/", local)[0] == 200 and fetch(base + "/static/stats.js", local)[0] == 200
        assert fetch(base + "/static/app.js", local)[0] == 404 and fetch(base + "/v1/spots", local)[0] == 404
        assert fetch(base + "/stats.json", {"Host": "localhost", "CF-Connecting-IP": "203.0.113.9"})[0] == 403   # came through the tunnel
        assert fetch(base + "/stats.json", {"Host": "surf.example.com"})[0] == 403
    finally:
        httpd.shutdown()
    ok = {"Host": "100.101.102.103:7875"}
    assert netguard.stats_allowed("100.101.102.105", ok) is None and netguard.stats_allowed("127.0.0.1", ok) is None
    assert netguard.stats_allowed("192.168.1.5", ok) and netguard.stats_allowed("8.8.8.8", ok)   # the LAN is not the tailnet
    assert netguard.stats_allowed("100.101.102.105", {**ok, "CF-Ray": "abc"})
