"""Step 16b: geometry - center, window-relative box, offscreen, DPI."""

from __future__ import annotations

import pytest

from snapshot_parser.geometry import (
    DpiInfo,
    center,
    element_geometry,
    format_bounds,
    is_offscreen,
    normalize_dpi,
    parse_rect,
    relative,
    union,
)
from snapshot_parser.model import Element, Rect, Snapshot


def test_center_is_the_middle_of_the_box():
    assert center(Rect(0, 0, 10, 10)) == (5, 5)
    assert center(Rect(100, 200, 40, 20)) == (120, 210)
    assert center(Rect(1842, 12, 46, 32)) == (1865, 28)


def test_relative_to_window():
    window = Rect(100, 50, 800, 600)
    assert relative(Rect(150, 70, 20, 20), window) == (50, 20)


def test_offscreen_detection():
    window = Rect(100, 50, 800, 600)
    assert is_offscreen(Rect(0, 0, 0, 0))
    assert is_offscreen(Rect(10, 10, 5, 0))
    # completely left of the window
    assert is_offscreen(Rect(0, 60, 50, 20), window=window)
    # completely below the window
    assert is_offscreen(Rect(200, 700, 20, 20), window=window)
    # partially visible is not offscreen
    assert not is_offscreen(Rect(90, 60, 50, 20), window=window)
    assert not is_offscreen(Rect(200, 200, 20, 20), window=window)


def test_offscreen_against_a_screen_rect():
    screen = Rect(0, 0, 1920, 1080)
    # completely outside the screen on the right
    assert is_offscreen(Rect(1950, 10, 100, 20), screen=screen)
    # hanging off the right edge but still partially visible
    assert not is_offscreen(Rect(1900, 10, 100, 20), screen=screen)
    assert not is_offscreen(Rect(1800, 10, 100, 20), screen=screen)


def test_element_geometry_record():
    el = Element(ref="e6", role="button", name="Close", rect=Rect(1764, 8, 46, 32))
    geo = element_geometry(el, window=Rect(0, 0, 1920, 1080), snapshot_number=4)
    assert geo == {
        "x": 1764, "y": 8, "width": 46, "height": 32,
        "cx": 1787, "cy": 24, "rx": 1764, "ry": 8,
        "offscreen": False, "snapshot": 4,
    }


def test_element_geometry_flags_a_zero_size_box():
    el = Element(ref="e1", rect=Rect(10, 10, 0, 0))
    assert element_geometry(el)["offscreen"] is True


def test_format_bounds_is_the_contract_form():
    assert format_bounds(Rect(1842, 12, 46, 32)) == "1842,12 46x32"
    assert "win 1764,8" in format_bounds(Rect(1764, 8, 46, 32), window=Rect(0, 0, 1920, 1080))
    assert "offscreen" in format_bounds(Rect(0, 0, 0, 0))


def test_dpi_scaling_round_trips():
    dpi = normalize_dpi(1.5)
    physical = dpi.to_physical(Rect(100, 100, 200, 100))
    assert physical == Rect(150, 150, 300, 150)
    assert dpi.to_logical(physical) == Rect(100, 100, 200, 100)


def test_dpi_defaults_are_safe():
    assert normalize_dpi(0).scale == 1.0
    assert normalize_dpi(-2).scale == 1.0
    assert DpiInfo().scale == 1.0
    assert normalize_dpi(2.0).to_physical(Rect(1, 1, 10, 10)) == Rect(2, 2, 20, 20)


def test_multi_monitor_negative_origin_is_preserved():
    dpi = normalize_dpi(1.0, origin=(-1920, 0), multi_monitor=True)
    assert dpi.origin == (-1920, 0)
    assert dpi.multi_monitor


@pytest.mark.parametrize(
    "value,expected",
    [
        (Rect(1, 2, 3, 4), Rect(1, 2, 3, 4)),
        ({"x": 1, "y": 2, "width": 3, "height": 4}, Rect(1, 2, 3, 4)),
        ({"x": 1, "y": 2, "w": 3, "h": 4}, Rect(1, 2, 3, 4)),
        ([1, 2, 3, 4], Rect(1, 2, 3, 4)),
        ((1, 2, 3, 4), Rect(1, 2, 3, 4)),
        (None, Rect()),
    ],
)
def test_parse_rect(value, expected):
    assert parse_rect(value) == expected


def test_union_of_boxes():
    assert union([Rect(0, 0, 10, 10), Rect(20, 20, 10, 10)]) == Rect(0, 0, 30, 30)
    assert union([]) == Rect()


def test_moving_the_window_moves_the_element():
    """Step 22b: geometry is relative to the screen, so a window move shows up."""
    el = Element(ref="e6", rect=Rect(1764, 8, 46, 32))
    before = element_geometry(el, window=Rect(0, 0, 1920, 1080))
    el.rect = el.rect.moved_by(100, 40)
    after = element_geometry(el, window=Rect(100, 40, 1920, 1080))
    assert after["x"] - before["x"] == 100
    assert after["y"] - before["y"] == 40
    assert (after["rx"], after["ry"]) == (before["rx"], before["ry"])
