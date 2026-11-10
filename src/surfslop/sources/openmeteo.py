"""Open-Meteo: three global wave models at each region's offshore point, and the wind, weather and
sun times at every spot.

Free, no key (open-meteo.com). Two requests cover all spots: one marine call for the offshore
points, one weather call for the spots' wind, air temperature, sky, sunrise and sunset.
"""

from __future__ import annotations

from .. import net
from ..spots import REGIONS, SPOTS, Spot
from . import guarded, result

MODELS = (
    ("ncep_gfswave025", "GFS-Wave (NOAA)"),
    ("ecmwf_wam025", "ECMWF WAM"),
    ("meteofrance_wave", "MFWAM (Météo-France)"),
)
TTL = 3600
_FIELDS = ("wave_height,wave_direction,wave_period,wave_peak_period,swell_wave_height,swell_wave_direction,"
           "swell_wave_period,swell_wave_peak_period,secondary_swell_wave_height,secondary_swell_wave_period,"
           "secondary_swell_wave_direction")
_POINTS = sorted({r.offshore_point for r in REGIONS.values()})


def _marine_url() -> str:
    lat = ",".join(str(p[0]) for p in _POINTS)
    lon = ",".join(str(p[1]) for p in _POINTS)
    models = ",".join(m for m, _ in MODELS)
    return (f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}&hourly={_FIELDS}"
            f"&models={models}&timeformat=unixtime&forecast_days=8&length_unit=imperial")


def _wind_url() -> str:
    lat = ",".join(f"{s.lat:.4f}" for s in SPOTS)
    lon = ",".join(f"{s.lon:.4f}" for s in SPOTS)
    return (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,weather_code,is_day"
            "&daily=sunrise,sunset&wind_speed_unit=kn&temperature_unit=fahrenheit"
            "&timezone=America%2FLos_Angeles&timeformat=unixtime&forecast_days=8")


def _swells(hourly: dict, model: str, i: int) -> list:
    def v(name):
        series = hourly.get(f"{name}_{model}")
        return series[i] if series else None

    total, total_dir = v("wave_height"), v("wave_direction")
    mean_period, peak = v("wave_period"), v("wave_peak_period")
    swell, swell_dir = v("swell_wave_height"), v("swell_wave_direction")
    if swell is None:  # ECMWF publishes the whole sea state only
        return [(total, peak or mean_period, total_dir)] if total else []
    period = v("swell_wave_peak_period") or v("swell_wave_period")
    if mean_period and total and swell >= 0.7 * total:
        period = max(period or 0, mean_period)  # MFWAM's swell period is a mean, shorter than the peak
    trains = [(swell, period, swell_dir)] if swell and period else []
    second = v("secondary_swell_wave_height")
    if second and second >= 0.5 and v("secondary_swell_wave_period"):
        trains.append((second, v("secondary_swell_wave_period"), v("secondary_swell_wave_direction")))
    return trains


def _spot_data(spot: Spot) -> dict | None:
    try:
        data, _ = net.get_json(_wind_url(), ttl=TTL)
    except net.FetchError:
        return None
    data = data if isinstance(data, list) else [data]
    return data[SPOTS.index(spot)]


def spot_wind(spot: Spot) -> dict:
    """{epoch: (speed_kt, gust_kt, from_deg)} at the spot; {} when the request fails."""
    data = _spot_data(spot)
    if not data:
        return {}
    hourly = data["hourly"]
    return {t: (s, g, d) for t, s, g, d in zip(hourly["time"], hourly["wind_speed_10m"],
                                               hourly["wind_gusts_10m"], hourly["wind_direction_10m"])
            if s is not None and d is not None}


def spot_weather(spot: Spot) -> dict:
    """{"hours": {epoch: (temp_f, wmo_code, is_day)}, "sun": [(sunrise, sunset), …]} at the spot;
    empty when the request fails."""
    data = _spot_data(spot)
    if not data:
        return {"hours": {}, "sun": []}
    hourly, daily = data["hourly"], data.get("daily") or {}
    hours = {t: (temp, code, bool(day)) for t, temp, code, day in zip(
        hourly["time"], hourly.get("temperature_2m") or [], hourly.get("weather_code") or [], hourly.get("is_day") or [])
        if temp is not None}
    sun = [(rise, down) for rise, down in zip(daily.get("sunrise") or [], daily.get("sunset") or []) if rise and down]
    return {"hours": hours, "sun": sun}


@guarded
def fetch(spot: Spot) -> list:
    data, fetched = net.get_json(_marine_url(), ttl=TTL)
    data = data if isinstance(data, list) else [data]
    hourly = data[_POINTS.index(REGIONS[spot.region].offshore_point)]["hourly"]
    wind = spot_wind(spot)
    out = []
    for model, label in MODELS:
        res = result(f"openmeteo-{model.split('_')[0]}", f"Open-Meteo · {label}", "https://open-meteo.com/en/docs/marine-weather-api",
                     "ocean", group="openmeteo")
        res["fetched"] = fetched
        for i, t in enumerate(hourly["time"]):
            swells = _swells(hourly, model, i)
            if swells:
                res["points"].append({"t": t, "swells": swells, "wind": wind.get(t)})
        if not res["points"]:
            res["error"] = "no data for this model right now"
        out.append(res)
    return out
