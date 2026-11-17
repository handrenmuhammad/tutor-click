"""The doctor's report and the checks that can be judged without a desktop."""
import math

import numpy as np

from tutor_click import doctor
from tutor_click.doctor import POINTS, RING, Problem, Report, Skip, ring_points, tone


def test_a_report_says_what_passed_what_failed_and_what_was_left_out():
    r = Report()
    r.note("system", "Darwin 15.1, arm64")
    r.check("screenshot", lambda: "1568x980")
    r.check("permissions", lambda: (_ for _ in ()).throw(Problem("Accessibility is OFF. Switch it on.")))
    r.check("mouse and keyboard", lambda: (_ for _ in ()).throw(Skip("add --input")))
    r.check("windows", lambda: {}["missing"])
    text = r.text()
    assert "         system: Darwin 15.1, arm64" in text
    assert "ok       screenshot: 1568x980 [" in text
    assert "FAILED   permissions: Accessibility is OFF. Switch it on. [" in text
    assert "skipped  mouse and keyboard: add --input [" in text
    assert "FAILED   windows: KeyError: 'missing' (at test_doctor.py:" in text
    assert text.endswith("2 check(s) FAILED")
    assert r.failed == 2


def test_a_clean_report_says_so():
    r = Report()
    r.check("screen", lambda: "ok")
    assert r.text().endswith("every check that ran passed")


def screen_with_ring(cx, cy, factor, colour=(255, 199, 38)) -> np.ndarray:
    px = np.full((656, 1567, 3), 40, np.uint8)
    for k in range(720):
        a = 2 * math.pi * k / 720
        px[round((cy + RING * math.cos(a)) * factor), round((cx + RING * math.sin(a)) * factor)] = colour
    return px


def test_the_pointer_ring_is_found_in_a_scaled_down_screenshot_and_only_where_it_is():
    px = screen_with_ring(1720, 720, 0.4558)
    assert ring_points(px, 1720, 720, 0.4558) == POINTS
    assert ring_points(px, 600, 300, 0.4558) == 0
    assert ring_points(np.full((656, 1567, 3), 240, np.uint8), 1720, 720, 0.4558) == 0  # a white page is not a ring
    assert ring_points(screen_with_ring(1720, 720, 0.4558, colour=(70, 120, 250)), 1720, 720, 0.4558) == 0  # nor is a blue circle


def test_the_ring_check_does_not_fall_over_at_the_edge_of_the_screen():
    assert ring_points(np.full((100, 100, 3), 40, np.uint8), 0, 0, 1.0) == 0


def test_the_beep_is_whole_16_bit_samples_and_not_silent():
    samples = tone(24000)
    assert len(samples) == 2 * int(24000 * 0.35)
    assert max(samples) > 0


def test_unknown_flags_are_turned_away_before_anything_runs():
    import pytest

    with pytest.raises(SystemExit, match="usage: tutor-click doctor"):
        doctor.run(None, ["--everything"])
