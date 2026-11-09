import time

import pytest

from surfslop import fusion, net
from surfslop.sources import result, surfline

REAL_DOWNLOAD = net._download  # for the few tests that exercise it against a fake urlopen


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Every test gets its own home folder, and none may touch the network."""
    monkeypatch.setenv("SURFSLOP_HOME", str(tmp_path))

    def refuse(url, headers, timeout):
        raise net.FetchError(f"tests don't use the network ({url})")

    monkeypatch.setattr(net, "_download", refuse)
    fusion._cache.clear()
    surfline._queue.clear(), surfline._forced.clear(), surfline._busy.clear()


def midnight() -> int:
    """Local midnight today, the first hour of fusion's grid."""
    return fusion._grid(time.time())[0][0]


def ocean_source(id="nws", height=6.0, period=14.0, direction=290.0, hours=120, wind=None, group=None):
    res = result(id, id, "https://example.test", "ocean", group=group)
    res["fetched"] = time.time()
    res["points"] = [{"t": midnight() + i * 3600, "swells": [(height, period, direction)], "wind": wind} for i in range(hours)]
    return res


def spot_source(id="surfline", lo=4.0, hi=6.0, hours=120, rating=None, note=None):
    res = result(id, id, "https://example.test", "spot")
    res["fetched"] = time.time()
    res["points"] = [{"t": midnight() + i * 3600, "surf": (lo, hi), "rating": rating} for i in range(hours)]
    if note:
        res["notes"] = {time.strftime("%Y-%m-%d", time.localtime(midnight() + 12 * 3600)): note}
    return res
