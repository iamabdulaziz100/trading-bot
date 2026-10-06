import pytest

from app.core.aoi import build_zones, correct_side, interacts, is_confined, is_valid_zone
from app.models import BEARISH, BULLISH, BUY, SELL, StructureSnapshot, Zone

PIP = 0.0001


def test_two_touch_invalid_three_touch_valid():
    assert [z for z in build_zones([1.1000, 1.1005], PIP, "1D") if z.valid] == []
    zones = build_zones([1.1000, 1.1005, 1.1008], PIP, "1D")  # mixed S/R pivots count alike
    valid = [z for z in zones if z.valid]
    assert len(valid) == 1 and valid[0].touches == 3


def test_narrow_zone_padded_to_min_width():
    zones = build_zones([1.1000, 1.1002, 1.1004], PIP, "1D")  # 4-pip spread
    assert len(zones) == 1
    z = zones[0]
    assert z.width == pytest.approx(5 * PIP)
    assert z.center == pytest.approx(1.1002)
    assert z.valid and z.touches == 3


def test_width_cap_exact():
    assert is_valid_zone(1.1000, 1.1060, 3, PIP)  # 60 pips: valid
    assert not is_valid_zone(1.1000, 1.1061, 3, PIP)  # 61 pips: invalid
    assert not is_valid_zone(1.1000, 1.10049, 3, PIP)  # < 5 pips: invalid
    assert not is_valid_zone(1.1000, 1.1030, 2, PIP)  # 2 touches: invalid


def test_61_pip_cluster_produces_no_valid_zone():
    zones = build_zones([1.1000, 1.1030, 1.1061], PIP, "1W")
    assert [z for z in zones if z.valid] == []


def test_clusters_and_merge():
    prices = [1.1000, 1.1003, 1.1006, 1.1200, 1.1204, 1.1207, 1.1209]
    zones = build_zones(prices, PIP, "1D", merge_tolerance=10 * PIP)
    assert [z.touches for z in zones] == [3, 4]
    # two clusters whose centres are within the merge tolerance become one zone
    close = [1.1000, 1.1002, 1.1004, 1.1090, 1.1093, 1.1096]
    merged = build_zones(close, PIP, "1D", merge_tolerance=100 * PIP, max_width_pips=150)
    assert len(merged) == 1 and merged[0].touches == 6


def test_jpy_pip_scaling():
    zones = build_zones([150.00, 150.02, 150.04], 0.01, "1D")
    assert zones[0].valid and zones[0].width == pytest.approx(0.05)


def test_boundary_confinement():
    bull = StructureSnapshot(BULLISH, 1.1200, 1.1000, None, None, 10)
    inside = Zone("1D", 1.1050, 1.1080, 3, True)
    touching_hh = Zone("1D", 1.1150, 1.1200, 3, True)  # z_max ≥ active_HH → rejected
    below_hl = Zone("1D", 1.0990, 1.1020, 3, True)
    assert is_confined(inside, bull, BUY)
    assert not is_confined(touching_hh, bull, BUY)
    assert not is_confined(below_hl, bull, BUY)
    assert not is_confined(inside, bull, SELL)  # wrong structure state for a short context
    bear = StructureSnapshot(BEARISH, None, None, 1.1200, 1.1000, 10)
    assert is_confined(inside, bear, SELL)
    assert not is_confined(Zone("1D", 1.0995, 1.1030, 3, True), bear, SELL)  # z_min ≤ active_LL


def test_interaction_and_cardinal_side():
    z = Zone("1D", 1.1000, 1.1010, 3, True)
    assert interacts(z, high=1.1030, low=1.1008)
    assert not interacts(z, high=1.1030, low=1.1011)
    assert correct_side(z, close=1.1015, direction=BUY)  # above → support-side
    assert not correct_side(z, close=1.0995, direction=BUY)  # closed below support
    assert correct_side(z, close=1.0995, direction=SELL)
    assert not correct_side(z, close=1.1015, direction=SELL)
