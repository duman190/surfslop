import pytest

from conftest import midnight, ocean_source, spot_source
from surfslop import ai, config, fusion, obs, surf, text
from surfslop.spots import BY_ID

LANE = BY_ID["steamer-lane"]


def test_series_interpolates_inside_gaps_only():
    s = fusion.Series([(0, (2.0, 4.0)), (6 * 3600, (4.0, 8.0)), (40 * 3600, (1.0, 1.0))])
    assert s.at(3 * 3600) == (3.0, 6.0)
    assert s.at(20 * 3600) is None            # a 34-hour hole is not bridged
    assert s.at(-2 * 3600) == (2.0, 4.0) and s.at(-5 * 3600) is None
    assert s.near(5 * 3600) == (4.0, 8.0) and s.near(20 * 3600) is None
    assert not fusion.Series([])


def test_fuse_weights_sources_and_reports_agreement():
    low, high = spot_source("nws", 4, 6), spot_source("surfforecast", 8, 10)
    data = fusion.fuse(LANE, [low, high], tides=[], stored={})
    day = data["days"][0]
    # NWS weighs 1.5, surf-forecast 0.8: the fused height sits nearer NWS's.
    assert day["surf_min"] == pytest.approx((1.5 * 4 + 0.8 * 8) / 2.3, abs=0.05)
    assert day["by_source"]["nws"]["surf"] == "4-6" and day["by_source"]["surfforecast"]["surf"] == "8-10"
    assert day["confidence"] == "medium" and day["families"] == 2 and day["spread"] > 0.2
    agree = fusion.fuse(LANE, [spot_source("nws", 4, 6), spot_source("surfforecast", 4, 6),
                               spot_source("openmeteo-ncep", 4.2, 6.2)], tides=[], stored={})
    assert agree["days"][0]["confidence"] == "high"
    alone = fusion.fuse(LANE, [spot_source("nws", 4, 6)], tides=[], stored={})
    assert alone["days"][0]["confidence"] == "low"      # one source can't confirm itself


def test_ocean_swell_is_brought_to_the_break():
    data = fusion.fuse(LANE, [ocean_source("nws", 6, 14, 300)], tides=[], stored={})
    center = surf.face_height(LANE.exposure, [(6, 14, 300)])
    hour = data["hours"][10]
    assert (hour["surf_min"] + hour["surf_max"]) / 2 == pytest.approx(center, abs=0.1)
    assert hour["swells"][0] == {"height": 6.0, "period": 14.0, "dir": 300.0, "from": "WNW"}
    capitola = fusion.fuse(BY_ID["capitola"], [ocean_source("nws", 6, 14, 300)], tides=[], stored={})
    assert capitola["hours"][10]["surf_max"] < hour["surf_min"]      # the same swell, far smaller in the lee


def test_wind_makes_the_surface_and_tides_are_listed():
    onshore = ocean_source("openmeteo-ncep", group="openmeteo", wind=(15.0, 20.0, (LANE.offshore + 180) % 360))
    t0 = midnight()
    tides = [(t0 - 3 * 3600, 5.0, "H"), (t0 + 3 * 3600, 1.0, "L"), (t0 + 9 * 3600, 5.0, "H"), (t0 + 15 * 3600, 0.0, "L"),
             (t0 + 21 * 3600, 4.0, "H"), (t0 + 27 * 3600, 1.0, "L")]
    data = fusion.fuse(LANE, [onshore], tides=tides, stored={})
    day = data["days"][0]
    assert day["quality"] == "blown" and day["periods"]["morning"]["wind"]["type"] == "onshore"
    assert [t["type"] for t in day["tides"]] == ["low", "high", "low", "high"]
    assert data["hours"][0]["tide"] == pytest.approx(3.0, abs=0.01)   # halfway down from high to low
    assert obs.tide_at(tides, t0 + 3 * 3600) == pytest.approx(1.0)


def test_calibration_scales_other_sources_to_surfline():
    stored = {}
    results = [spot_source("surfline", 4, 6), ocean_source("nws", 6, 14, 300)]
    raw = sum(surf.face_range(surf.face_height(LANE.exposure, [(6, 14, 300)]))) / 2
    data = fusion.fuse(LANE, results, tides=[], stored=stored)
    scale = stored["steamer-lane"]["nws"]["scale"]
    assert scale == pytest.approx(5 / raw, rel=0.02)
    assert data["days"][0]["by_source"]["nws"]["surf"] == "4-6"       # NWS now reads like Surfline here
    # With Surfline gone (switched off, or past its five days), the remembered scale still applies.
    later = fusion.fuse(LANE, [ocean_source("nws", 6, 14, 300)], tides=[], stored=stored)
    assert later["days"][0]["by_source"]["nws"]["surf"] == "4-6"
    assert next(s for s in later["sources"] if s["id"] == "nws")["scale"] == scale


def test_failed_source_is_reported_not_fused():
    broken = spot_source("surfforecast")
    broken["points"], broken["error"] = [], "surf-forecast.com answered 503"
    data = fusion.fuse(LANE, [broken, ocean_source("nws")], tides=[], stored={})
    status = {s["id"]: s for s in data["sources"]}
    assert status["surfforecast"]["ok"] is False and "503" in status["surfforecast"]["error"]
    assert list(data["days"][0]["by_source"]) == ["nws"]


def test_surfline_is_off_until_switched_on():
    cfg = config.load()
    assert not cfg.enabled("surfline") and cfg.enabled("nws")
    cfg.disabled = []
    config.save(cfg)
    assert config.load().enabled("surfline")
    assert oct(config.config_path().stat().st_mode & 0o777) == "0o600"


def test_markdown_and_ai_block_carry_the_numbers():
    data = fusion.fuse(LANE, [spot_source("surfline", 4, 6, rating="Fair", note="Fun waves on the tide push."),
                              ocean_source("nws", 6, 14, 300)], tides=[], stored={})
    data.update(tide_station="Santa Cruz", buoys=[], off=[], now=data["hours"][12])
    md = text.forecast_md(data, "**Bottom line:** fun.")
    assert "# Steamer Lane surf forecast" in md and "| Surfline | NWS |" in md and "Fun waves on the tide push." in md
    assert "**Bottom line:** fun." in md
    block = text.ai_block(data)
    assert "SPOT: Steamer Lane" in block and "Surfline 4-6 [Fair]" in block and "confidence" in block
    assert "WRITTEN FORECASTS" not in block               # nothing written came with these sources
    day = data["days"][0]["date"]
    data["writeups"] = [{"source": "Surfline", "day": day, "text": "Fun waves on the tide push. Clean till noon."},
                        {"source": "surf-forecast.com", "day": None, "text": "The primary swell is predicted to be 3 ft."},
                        {"source": "Surfline", "day": "1999-01-01", "text": "an old note"}]
    block = text.ai_block(data)
    assert f"Surfline's forecaster, {data['days'][0]['label']}: Fun waves on the tide push. Clean till noon." in block
    assert "surf-forecast.com: The primary swell is predicted to be 3 ft." in block and "an old note" not in block


def test_overview_stands_for_the_edition_and_is_rewritten_when_a_source_arrives():
    cfg = config.load()
    written = []

    def overview(data):
        hit = ai.spot_cached(data, cfg)
        if hit:
            return hit["text"]
        written.append(ai._shape(data))
        config.write_private(ai._cache_path(ai._name(data, data["spot"]["id"])), ai.json.dumps({
            "system": ai._digest(ai.SPOT_SYSTEM), "shape": ai._shape(data), "text": f"overview {len(written)}",
            "generated": int(ai.time.time())}))
        return f"overview {len(written)}"

    def forecast(results, public=False):
        data = fusion.fuse(LANE, results, tides=[], stored={})
        data.update(tide_station="Santa Cruz", buoys=[], off=[], now=data["hours"][12], public=public)
        return data

    without = forecast([ocean_source("nws", 6, 14, 300)])
    assert overview(without) == "overview 1" and overview(without) == "overview 1"          # kept
    assert overview(forecast([ocean_source("nws", 9, 14, 300)])) == "overview 1"            # the numbers moved: kept for the day
    with_surfline = forecast([spot_source("surfline", 4, 6), ocean_source("nws", 6, 14, 300)])
    assert overview(with_surfline) == "overview 2" and written == ["nws|0", "surfline,nws|0"]  # a new source: rewritten
    # The public portal keeps its own overview, even for the same spot and the same sources.
    assert overview(forecast([ocean_source("nws", 6, 14, 300)], public=True)) == "overview 3"
    assert ai._cache_path("pub-steamer-lane").exists() and ai._cache_path("steamer-lane").exists()
    # Tomorrow's edition starts over.
    entry = ai.json.loads(ai._cache_path("steamer-lane").read_text())
    entry["generated"] -= 25 * 3600
    config.write_private(ai._cache_path("steamer-lane"), ai.json.dumps(entry))
    assert ai.spot_cached(with_surfline, cfg) is None


def test_spots_fused_at_once_all_keep_their_calibration():
    import json
    import threading
    from surfslop.spots import FEATURED

    def one(spot):
        fusion.fuse(spot, [spot_source("surfline", 4, 6), ocean_source("nws", 6, 14, 300)], tides=[])   # stored=None: on disk

    threads = [threading.Thread(target=one, args=(spot,)) for spot in FEATURED for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    saved = json.loads(fusion._calibration_path().read_text())
    assert sorted(saved) == sorted(s.id for s in FEATURED) and all("nws" in entry for entry in saved.values())
    assert not list(config.home().glob("*.tmp"))


def test_swell_trains_are_matched_by_direction_then_averaged():
    wnw_a, wnw_b, south = (8.0, 14.0, 290.0), (10.0, 16.0, 300.0), (2.0, 15.0, 200.0)
    trains = fusion.fuse_swells(LANE, [(1.5, [wnw_a, south]), (0.5, [south, wnw_b])])     # listed in different orders
    assert trains[0] == {"height": 8.5, "period": 14.0, "dir": 292.0, "from": "WNW"}      # 8 and 10 at weights 1.5 and 0.5
    assert trains[1] == {"height": 2.0, "period": 15.0, "dir": 200.0, "from": "SSW"}
    assert fusion.fuse_swells(LANE, [(1.0, [wnw_a, (0.3, 9.0, 200.0)])]) == [trains[0] | {"height": 8.0, "dir": 290.0}]   # a ripple is not a train
    assert fusion.fuse_swells(LANE, []) == []


def test_every_hour_carries_swell_energy_consistency_weather_and_the_day_its_sun():
    t0 = midnight()
    weather = {"hours": {t0 + i * 3600: (55.0 + i % 24, 2 if 6 <= i % 24 < 18 else 0, 6 <= i % 24 < 18) for i in range(72)},
               "sun": [(t0 + 6 * 3600 + 40 * 60, t0 + 17 * 3600 + 120)]}
    wind = (8.0, 12.0, LANE.offshore)
    data = fusion.fuse(LANE, [spot_source("surfline", 4, 6), ocean_source("nws", 6, 14, 300),
                              ocean_source("openmeteo-ncep", 8, 14, 300, wind=wind, group="openmeteo")], tides=[], stored={}, weather=weather)
    hour = data["hours"][12]
    assert hour["swells"] == [{"height": 6.7, "period": 14.0, "dir": 300.0, "from": "WNW"}]   # NWS 6 (1.5) and GFS 8 (0.8); not Surfline
    at_sea = surf.wave_energy(6.7, 14)
    assert hour["energy"]["offshore"] == round(at_sea) and 0 < hour["energy"]["nearshore"] < hour["energy"]["offshore"]
    mid = (hour["surf_min"] + hour["surf_max"]) / 2
    assert hour["consistency"] == surf.consistency(mid, 14.0)
    assert hour["weather"] == {"temp_f": 67, "sky": "partly cloudy", "night": False} and data["hours"][2]["weather"]["night"]
    assert hour["wind"]["type"] == "offshore" and "weather" not in data["hours"][100]         # past the weather forecast: left out
    day = data["days"][0]
    assert day["sun"]["sunrise"] == "6:40am" and day["sun"]["sunset"] == "5:02pm"
    assert day["weather"] == {"low": 55, "high": 78, "sky": "partly cloudy"}
    assert day["consistency"] == hour["consistency"] and day["energy"] == hour["energy"]["nearshore"]
    plain = fusion.fuse(LANE, [ocean_source("nws", 6, 14, 300)], tides=[], stored={})          # no weather given
    assert "weather" not in plain["hours"][12] and plain["days"][0]["weather"] is None and plain["days"][0]["sun"] is None
    data.update(tide_station="Santa Cruz", buoys=[], off=[], now=hour)
    md = text.forecast_md(data)
    assert "## Hour by hour, next 48 hours" in md and "6.7 ft @ 14 s WNW" in md and "67°F partly cloudy" in md
    assert f"{hour['energy']['nearshore']} / {hour['energy']['offshore']}" in md and "Sunrise 6:40am, sunset 5:02pm." in md


def test_surf_forecast_is_never_scaled_to_surfline():
    stored = {}
    data = fusion.fuse(LANE, [spot_source("surfline", 6, 8), spot_source("surfforecast", 2, 3), ocean_source("nws", 6, 14, 300)],
                       tides=[], stored=stored)
    assert set(stored["steamer-lane"]) == {"nws"}                              # only the open-ocean source is tuned
    assert data["days"][0]["by_source"]["surfforecast"]["surf"] == "2-3"        # its own opinion, as it gave it
    stored["steamer-lane"]["surfforecast"] = {"scale": 2.4}                     # a scale saved by an older version is ignored
    again = fusion.fuse(LANE, [spot_source("surfforecast", 2, 3)], tides=[], stored=stored)
    assert again["days"][0]["by_source"]["surfforecast"]["surf"] == "2-3"


def test_the_saved_edition_stands_in_when_no_source_answers(monkeypatch):
    from surfslop.sources import nws as nws_source
    data = fusion.fuse(LANE, [ocean_source("nws", 6, 14, 300)], tides=[], stored={})
    data.update(tide_station="Santa Cruz", buoys=[], off=[], now=data["hours"][12], public=False)
    fusion.save_edition(data)
    assert fusion.load_edition(LANE)["days"][0]["surf"] == data["days"][0]["surf"] and fusion.load_edition(LANE, public=True) is None
    cfg = config.load()                                    # offline (conftest): every source fails
    served = fusion.forecast(LANE, cfg)
    assert served["edition"] and served["days"][0]["surf"] == data["days"][0]["surf"] and len(served["hours"]) == len(data["hours"])
    old = fusion.json.loads(fusion._edition_path(LANE, False).read_text())
    old["edition"] -= 40 * 3600
    fusion._edition_path(LANE, False).write_text(fusion.json.dumps(old))
    fusion._cache.clear()
    assert fusion.forecast(LANE, cfg)["days"] == []        # too old to show as today's
