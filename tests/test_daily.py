from datetime import datetime
from zoneinfo import ZoneInfo

from surfslop import ai, config, daily, fusion, net
from surfslop.sources import surfline
from surfslop.spots import FEATURED

PT = ZoneInfo("America/Los_Angeles")


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=PT).timestamp()


def test_an_edition_runs_from_four_to_four():
    assert config.edition_start("04:00", at(10, 5, 30)) == at(10, 4)
    assert config.edition_start("04:00", at(10, 3, 59)) == at(9, 4)       # before 4 it is still yesterday's
    assert config.edition_start("04:00", at(10, 4)) == at(10, 4)
    assert config.edition_start("06:30", at(10, 12)) == at(10, 6, 30)
    assert config.edition_start("nonsense", at(10, 12)) == at(10, 4)


def fakes(monkeypatch):
    calls = {"surfline": [], "forecast": [], "overviews": []}
    monkeypatch.setattr(surfline, "request", lambda spot, force=False: calls["surfline"].append((spot.id, force)))
    monkeypatch.setattr(surfline, "pending", lambda: 0)

    def forecast(spot, cfg=None, refresh=False, public=False):
        calls["forecast"].append((spot.id, refresh, public))
        return {"spot": {"id": spot.id}, "public": public}

    monkeypatch.setattr(fusion, "forecast", forecast)
    monkeypatch.setattr(fusion, "overview", lambda cfg=None, spots=FEATURED, days=3, public=False: {"public": public})
    monkeypatch.setattr(ai, "spot_stream", lambda data, cfg: calls["overviews"].append(("spot", data["public"])) or iter(["words"]))
    monkeypatch.setattr(ai, "region_stream", lambda data, cfg: calls["overviews"].append(("region", data["public"])) or iter(["words"]))
    return calls


def test_the_edition_reads_surfline_fuses_and_writes_every_overview(monkeypatch):
    calls = fakes(monkeypatch)
    cfg = config.load()
    cfg.disabled = []
    assert daily.due(cfg)
    done = daily.run(cfg)
    assert calls["surfline"] == [(s.id, True) for s in FEATURED]                      # today's copy, for the seven
    assert calls["forecast"] == [(s.id, True, False) for s in FEATURED]
    assert calls["overviews"] == [("spot", False)] * 7 + [("region", False)]
    assert done["spots"] == 7 and done["overviews"] == 8
    assert not daily.due(cfg) and daily.due(cfg, now=daily.built() + 25 * 3600)       # done for today, due tomorrow
    assert daily.due(cfg, public=True)                                                # the public edition is its own


def test_the_public_edition_never_touches_surfline(monkeypatch):
    calls = fakes(monkeypatch)
    cfg = config.load()
    cfg.disabled = []
    daily.run(cfg, public=True)
    assert calls["surfline"] == [] and calls["forecast"] == [(s.id, True, True) for s in FEATURED]
    assert calls["overviews"] == [("spot", True)] * 7 + [("region", True)]
    assert not daily.due(cfg, public=True) and daily.due(cfg)


def test_the_edition_does_not_wait_on_surfline_when_it_is_off_or_refusing(monkeypatch):
    calls = fakes(monkeypatch)
    cfg = config.load()                                    # Surfline is off by default
    daily.run(cfg)
    assert calls["surfline"] == [] and len(calls["forecast"]) == 7
    cfg.disabled = []
    monkeypatch.setattr(surfline, "pending", lambda: 7)    # the reader is stuck behind a refusal
    net.block(surfline.HOST, 3600, "refused")
    monkeypatch.setattr(daily, "SURFLINE_WAIT", 5)
    assert daily.run(cfg)["spots"] == 7                    # fused from the other sources, not held up
