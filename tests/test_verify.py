"""Whether the app's window changed between two screenshots."""
from images import jpeg

from tutor_click import verify
from tutor_click.platform.base import Monitor, Shot, Window

W, H = 800, 400
MON = Monitor("HDMI-A-1", 1600, 800, 1.0)  # shot px = logical px * 0.5
LEFT_HALF = Window("0xaa", "pinta", "Pinta", (0, 0), (800, 800))  # shot px 0..400 wide


def shot(*blocks) -> Shot:
    return Shot(jpeg(W, H, blocks), W, H, 0.5)


def rect(win=LEFT_HALF, mon=MON):
    return verify.window_rect(win, mon, shot())


def test_decoded_pixels_have_the_image_shape_and_content():
    px = verify.pixels(jpeg(W, H, ((100, 100, 40),)))
    assert px.shape == (H, W, 3)
    assert px[120, 120].min() > 240  # inside the white square
    assert px[300, 300].max() < 75  # grey background


def test_identical_screenshots_show_no_change():
    c = verify.compare(shot((100, 100, 40),), shot((100, 100, 40),), rect())
    assert (c.pixels, c.changed) == (0, False)


def test_a_change_inside_the_window_is_seen():
    c = verify.compare(shot(), shot((100, 100, 40),), rect())
    assert c.changed
    assert 1600 <= c.pixels <= 2000  # the 40x40 square plus a little JPEG ringing
    assert 0.01 < c.fraction < 0.02


def test_a_change_in_the_neighbouring_window_is_ignored():
    c = verify.compare(shot(), shot((600, 100, 40),), rect())
    assert not c.changed


def test_a_change_on_the_windows_outer_edge_is_ignored():
    # 2 px wide strip along the window's right edge: where the neighbour bleeds in after downscaling
    c = verify.compare(shot(), shot((398, 0, 2),), rect())
    assert not c.changed


def test_window_rect_scales_trims_the_edge_and_follows_the_monitor_origin():
    assert rect() == (4, 4, 396, 396)
    right_mon = Monitor("DP-1", 1600, 800, 1.0, x=-1600, y=0)
    win = Window("0xbb", "pinta", "Pinta", (-800, 100), (600, 500))
    assert rect(win, right_mon) == (404, 54, 696, 296)


def test_window_rect_is_clipped_to_the_screenshot():
    assert rect(Window("0xcc", "pinta", "Pinta", (1000, -200), (5000, 5000))) == (504, 0, 800, 400)


def test_a_window_that_is_not_on_this_monitor_counts_as_unchanged():
    off = Window("0xdd", "pinta", "Pinta", (5000, 0), (800, 800))
    assert not verify.compare(shot(), shot((100, 100, 40),), rect(off)).changed
