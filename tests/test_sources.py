import json
import urllib.error
import urllib.request

import pytest

from conftest import REAL_DOWNLOAD
from surfslop import net
from surfslop.sources import nws, openmeteo, surfforecast, surfline
from surfslop.spots import BY_ID


def _cell(inner, attrs=""):
    return f'<td class="forecast-table__cell" {attrs}>{inner}</td>'


def _swell(height, turn, period):
    return _cell(f'<div class="swell-icon" data-height="{height}"><svg><g class="swell-icon__arrow" transform="rotate({turn})">'
                 f'</g></svg></div><div>SW</div><div class="forecast-table__swell-period">{period}</div>')


def _wind(speed, turn):
    return _cell(f'<div class="wind-icon" data-speed="{speed}"><svg><g class="wind-icon__arrow" transform="rotate({turn})"></g></svg></div>')


def _row(name, cells):
    return f'<tr class="forecast-table__row" data-row="{name}">{"".join(cells)}</tr>'


PAGE = "<table>" + "".join([
    _row("days", [_cell("Mon", 'colspan="3" data-date="2026-11-09"'), _cell("Tue", 'colspan="1" data-date="2026-11-10"')]),
    _row("time", [_cell("AM", 'data-hour="9"'), _cell("PM", 'data-hour="15"'), _cell("Night", 'data-hour="21"'), _cell("AM", 'data-hour="9"')]),
    _row("rating", [_cell('<div class="star-rating__rating star-rating__rating--3">3</div>')] * 4),
    _row("swell", [_swell(1.0, 110, 14), _swell(1.5, 110, 15), _swell("", 0, 0), _swell(0.5, 37, 16)]),
    _row("wind", [_wind(10, 204), _wind(20, 90), _wind(5, 0), _wind(0, 0)]),
    _row("wind-state", [_cell('<div class="wind-state">cross-off</div>')] * 4),
]) + "</table>"


def test_surfforecast_table():
    points = surfforecast.parse(PAGE)
    assert [(p["date"], p["hour"]) for p in points] == [("2026-11-09", 9), ("2026-11-09", 15), ("2026-11-10", 9)]
    height, period, direction = points[0]["swells"][0]
    assert height == pytest.approx(3.28, abs=0.01) and period == 14 and direction == 290   # metres → feet; arrow → "from"
    speed, _, wind_from = points[0]["wind"]
    assert speed == pytest.approx(5.4, abs=0.05) and wind_from == 24                        # km/h → knots
    assert points[0]["rating"] == "3/10" and points[0]["wind_state"] == "cross-off"
    assert surfforecast.parse("<html>redesigned</html>") == []


def test_surfforecast_written_summary():
    page = (PAGE.replace("<table>", '<table><tr class="forecast-table__row" data-row="summary"><td colspan="9"><b>Short Range '
                         'Forecast</b> Mostly dry. Warm (max 24°C on Tue morning). Rain total 9mm.</td><td colspan="3"></td></tr>')
            + "<p>The most powerful waves expected at Here in the next 16 days are 2.3 m 15s on Friday.</p>"
            + "<p>Hotels near Here have rooms for every budget.</p><script>var p = '<p>The surf forecast for x</p>';</script>")
    assert surfforecast.summary(page) == [
        "Short Range Forecast Mostly dry. Warm (max 75°F on Tue morning). Rain total 9mm.",
        "The most powerful waves expected at Here in the next 16 days are 8 ft 15s on Friday."]
    assert surfforecast.summary("<html>redesigned</html>") == []


def test_nws_spans_become_hours():
    prop = {"values": [{"validTime": "2026-11-09T15:00:00+00:00/PT3H", "value": 2.0},
                       {"validTime": "2026-11-09T18:00:00+00:00/P1DT6H", "value": 3.0},
                       {"validTime": "2026-11-11T00:00:00+00:00/PT1H", "value": None}]}
    hours = nws._hourly(prop, scale=2)
    assert len(hours) == 3 + 30 and set(hours.values()) == {4.0, 6.0}
    assert nws._hourly(None) == {}


def test_openmeteo_picks_swell_or_whole_sea():
    def hourly(model, **values):
        return {f"{k}_{model}": [v] for k, v in values.items()}

    gfs = hourly("gfs", wave_height=9.2, wave_direction=289, wave_period=15.2, swell_wave_height=9.1, swell_wave_direction=291,
                 swell_wave_period=15.1, secondary_swell_wave_height=1.2, secondary_swell_wave_period=15.7,
                 secondary_swell_wave_direction=207)
    assert openmeteo._swells(gfs, "gfs", 0) == [(9.1, 15.2, 291), (1.2, 15.7, 207)]
    ecmwf = hourly("ec", wave_height=14.0, wave_direction=292, wave_period=13.8, wave_peak_period=15.1, swell_wave_height=None)
    assert openmeteo._swells(ecmwf, "ec", 0) == [(14.0, 15.1, 292)]
    assert openmeteo._swells(hourly("x", wave_height=None, swell_wave_height=None), "x", 0) == []


def test_surfline_normalize_converts_units_and_keeps_headlines():
    surf = {"surf": [{"timestamp": 100, "surf": {"min": 1, "max": 2, "raw": {"min": 1.0, "max": 2.0}}},
                     {"timestamp": 200, "surf": {"min": 2, "max": 3}}]}
    swells = {"swells": [{"timestamp": 100, "swells": [{"height": 0, "period": 0, "direction": 0, "impact": 0},
                                                       {"height": 1.0, "period": 9, "direction": 200, "impact": 0.2},
                                                       {"height": 3.0, "period": 15, "direction": 292, "impact": 0.8}]}]}
    wind = {"wind": [{"timestamp": 100, "speed": 10, "gust": 20, "direction": 350}]}
    rating = {"rating": [{"timestamp": 100, "rating": {"key": "FAIR_TO_GOOD", "value": 4}}]}
    conditions = {"conditions": [{"timestamp": 100, "forecastDay": "2026-11-09", "headline": " Fun surf. ",
                                  "observation": "<strong>AM:</strong> Chest high.<br>\nLight &amp; offshore."},
                                 {"timestamp": 200, "forecastDay": "2026-11-10", "headline": "", "observation": ""}]}
    points, notes, writeups = surfline.normalize(surf, swells, wind, rating, conditions, {"waveHeight": "M", "swellHeight": "M", "windSpeed": "KPH"})
    assert points[0]["surf"] == pytest.approx((3.28, 6.56), abs=0.01)
    assert points[0]["swells"][0] == pytest.approx((9.84, 15, 292), abs=0.01) and len(points[0]["swells"]) == 2
    assert points[0]["wind"] == pytest.approx((5.4, 10.8, 350), abs=0.01)
    assert points[0]["rating"] == "Fair To Good" and notes == {"2026-11-09": "Fun surf."}
    assert writeups == [{"day": "2026-11-09", "text": "Fun surf. AM: Chest high. Light & offshore."}]
    assert points[1]["surf"] == pytest.approx((6.56, 9.84), abs=0.01) and "wind" not in points[1]


def test_surfline_asks_only_for_the_free_days():
    assert surfline.FREE_DAYS == 5 and "accesstoken" not in open(surfline.__file__).read().replace('"accesstoken" not in', "")


def test_surfline_pages_read_the_disk_and_queue_the_spot():
    spot = BY_ID["steamer-lane"]
    surfline._queue.clear()
    res = surfline.fetch(spot)[0]                       # nothing on disk: no network either (conftest would raise)
    assert not res["points"] and "being read now" in res["error"] and res["pending"] and surfline._queue == [spot]
    answer = {"associated": {"units": {"waveHeight": "FT"}}, "data": {"surf": [{"timestamp": 100, "surf": {"min": 3, "max": 4}}]}}
    net._cache_file(surfline._url("surf", spot, True)).write_bytes(json.dumps(answer).encode())
    surfline._queue.clear()
    res = surfline.fetch(spot)[0]
    assert res["points"] == [{"t": 100, "surf": (3.0, 4.0)}] and res["error"] is None and res["fetched"]
    assert surfline._queue == [spot]                     # usable already, but four answers are still missing
    for kind, hourly in surfline.KINDS[1:]:
        net._cache_file(surfline._url(kind, spot, hourly)).write_bytes(b'{"data": {}}')
    surfline._queue.clear()
    assert surfline.fetch(spot)[0]["points"] and surfline._queue == []   # complete and fresh: not due again


def test_a_refusal_stops_everything_and_is_remembered(monkeypatch):
    spot, asked = BY_ID["steamer-lane"], []

    def refuse(request, timeout):
        asked.append(request.full_url)
        assert request.get_header("User-agent").startswith("surfslop/")     # says who it is
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(net, "_download", REAL_DOWNLOAD)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(net.FetchError):
        surfline.refresh(spot, pause=0)
    assert len(asked) == 1 and "days=5" in asked[0]      # stopped at the first refusal, of five requests
    assert "answered 403; not asking again until" in net.blocked(surfline.HOST)
    with pytest.raises(net.FetchError):
        net.get(surfline._url("wind", spot, True), ttl=0)
    assert len(asked) == 1                               # no second request, from this or any later process
    error = surfline.fetch(spot)[0]["error"]
    assert "HTTP 403" in error and "will try again after" in error and len(asked) == 1


def test_the_worker_reads_queued_spots_one_request_at_a_time(monkeypatch):
    import io
    import threading

    asked, stop = [], threading.Event()

    class Answer(io.BytesIO):
        headers = {}

    def answer(request, timeout):
        asked.append(request.full_url)
        kind = request.full_url.split("/forecasts/")[1].split("?")[0]
        rows = [{"timestamp": 100, "surf": {"min": 3, "max": 4}}] if kind == "surf" else []
        if len(asked) == 10:
            stop.set()                                   # both spots done: let the worker go
        return Answer(json.dumps({"data": {kind: rows}}).encode())

    monkeypatch.setattr(net, "_download", REAL_DOWNLOAD)
    monkeypatch.setattr(net, "MIN_GAP", 0)
    monkeypatch.setattr(urllib.request, "urlopen", answer)
    monkeypatch.setattr(surfline, "PAUSE", 0)
    monkeypatch.setattr(surfline, "SPOT_GAP", 0)
    lane, ob = BY_ID["steamer-lane"], BY_ID["ocean-beach"]
    surfline._queue[:] = [lane, ob]
    surfline.worker(stop, lambda: True)
    assert len(asked) == 10 and all("days=5" in url for url in asked) and surfline._queue == []
    assert [url.split("/forecasts/")[1].split("?")[0] for url in asked[:5]] == ["surf", "conditions", "rating", "wind", "swells"]
    assert lane.surfline in asked[0] and ob.surfline in asked[5]
    assert surfline.fetch(ob)[0]["points"] == [{"t": 100, "surf": (3.0, 4.0)}] and surfline._queue == []
    surfline._queue[:] = [lane]                          # switched off: the queue is left alone, nothing is asked
    off = threading.Event()
    threading.Timer(0.2, off.set).start()
    surfline.worker(off, lambda: False)
    assert len(asked) == 10 and surfline._queue == [lane]


def test_surfline_answers_with_nulls_or_surprises_never_break_the_page():
    surf = {"surf": [{"timestamp": 100, "surf": {"min": 8, "max": 12}}]}
    points, notes, writeups = surfline.normalize(surf, {"swells": None}, {"wind": None}, {"rating": None}, {"conditions": None}, {})
    assert points == [{"t": 100, "surf": (8.0, 12.0)}] and notes == {} and writeups == []   # Mavericks: no rating at all
    spot = BY_ID["mavericks"]
    for kind, hourly in surfline.KINDS:
        body = {"data": {"surf": "not a list"}} if kind == "surf" else {"data": {}}
        net._cache_file(surfline._url(kind, spot, hourly)).write_bytes(json.dumps(body).encode())
    res = surfline.fetch(spot)[0]
    assert res["points"] == [] and "couldn't read Surfline's answer" in res["error"]


def test_openmeteo_weather_and_sun_come_with_the_wind():
    from surfslop.spots import SPOTS
    spot = BY_ID["pleasure-point"]
    one = {"hourly": {"time": [100, 3700], "wind_speed_10m": [5.0, None], "wind_gusts_10m": [8.0, 9.0], "wind_direction_10m": [300, 310],
                      "temperature_2m": [61.4, None], "weather_code": [2, 3], "is_day": [1, 0]},
           "daily": {"time": [0], "sunrise": [24000], "sunset": [61000]}}
    net._cache_file(openmeteo._wind_url()).write_bytes(json.dumps([one] * len(SPOTS)).encode())
    assert openmeteo.spot_wind(spot) == {100: (5.0, 8.0, 300)}
    assert openmeteo.spot_weather(spot) == {"hours": {100: (61.4, 2, True)}, "sun": [(24000, 61000)]}
    assert "temperature_2m" in openmeteo._wind_url() and "sunrise" in openmeteo._wind_url()
    net._cache_file(openmeteo._wind_url()).unlink()
    assert openmeteo.spot_weather(spot) == {"hours": {}, "sun": []}          # unreachable: no weather, nothing breaks
