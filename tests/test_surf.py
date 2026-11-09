import pytest

from surfslop import surf
from surfslop.spots import BY_ID, FEATURED, SPOTS, find, search


def test_longer_period_breaks_bigger():
    assert surf.breaking_height(3, 8) == pytest.approx(4.3, abs=0.1)
    assert surf.breaking_height(3, 17) == pytest.approx(5.8, abs=0.1)
    assert surf.breaking_height(3, 17) > surf.breaking_height(3, 8)
    assert surf.breaking_height(0, 12) == 0 and surf.breaking_height(4, None) == 0


def test_exposure_follows_swell_direction():
    lane, ocean_beach = BY_ID["steamer-lane"].exposure, BY_ID["ocean-beach"].exposure
    assert surf.exposure(lane, 195) == lane[0] and surf.exposure(lane, 300) == lane[2]
    assert lane[1] < surf.exposure(lane, 230) < lane[0]          # between S and W
    assert surf.exposure(BY_ID["capitola"].exposure, 300) < 0.15  # tucked behind the point
    assert surf.exposure(ocean_beach, 300) > surf.exposure(lane, 300)
    assert surf.exposure(ocean_beach, 90) < 0.2                   # an east swell misses the coast


def test_swell_trains_add_by_energy():
    factors = (1.0, 1.0, 1.0)
    one = surf.face_height(factors, [(6, 14, 265)])
    two = surf.face_height(factors, [(6, 14, 265), (1, 14, 265)])
    assert one < two < one * 1.05
    assert surf.face_height(factors, [(6, 14, 265)], scale=0.5) == pytest.approx(one / 2)


@pytest.mark.parametrize("lo, hi, words", [(0.2, 0.5, "flat"), (2.6, 3.4, "3-4"), (3.4, 3.6, "3-4"),
                                           (7.6, 10.3, "8-10"), (5.8, 6.9, "6-8"), (10.6, 12.2, "11-14"), (None, 3, "–")])
def test_surf_text(lo, hi, words):
    assert surf.surf_text(lo, hi) == words


def test_wind_quality_depends_on_the_spots_facing():
    offshore = BY_ID["ocean-beach"].offshore  # an east wind is offshore at Ocean Beach
    assert surf.wind_type(85, 12, offshore) == "offshore" and surf.quality(85, 12, offshore) == "clean"
    assert surf.wind_type(280, 12, offshore) == "onshore" and surf.quality(280, 12, offshore) == "blown"
    assert surf.quality(280, 8, offshore) == "fair"
    assert surf.wind_type(280, 2, offshore) == "glassy" and surf.quality(280, 2, offshore) == "clean"
    assert surf.quality(None, None, offshore) == ""


def test_rating_rewards_size_period_and_clean_wind():
    assert surf.rate(0.5, 1, 12, "clean") == 0
    assert surf.rate(3, 4, 15, "clean") > surf.rate(3, 4, 7, "clean") > surf.rate(3, 4, 7, "blown")
    assert 0 <= surf.rate(12, 18, 17, "clean") <= 5


def test_compass_and_mean_direction():
    assert surf.compass(292) == "WNW" and surf.compass(359) == "N" and surf.compass_deg("sw") == 225
    assert surf.mean_direction([(350, 1), (10, 1)]) == pytest.approx(0, abs=0.01) or surf.mean_direction([(350, 1), (10, 1)]) == pytest.approx(360, abs=0.01)


def test_spot_catalog():
    assert [s.name for s in FEATURED] == ["Steamer Lane", "Pleasure Point", "Four Mile", "Waddell Creek",
                                          "Half Moon Bay", "Pacifica", "Ocean Beach"]
    assert len({s.id for s in SPOTS}) == len(SPOTS)
    assert find("OB").id == "ocean-beach" and find("linda mar").id == "linda-mar"
    assert find("Surfer's Beach").id == "half-moon-bay" and find("steamer-lane").id == "steamer-lane"
    assert find("nowhere") is None and search("capitola")[0].id == "capitola"
    assert all(0 < x <= 1 for s in SPOTS for x in s.exposure)
