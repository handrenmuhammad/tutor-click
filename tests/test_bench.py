"""How the benchmark decides whether what the probe received matches what was sent."""
from tutor_click.platform.hyprland.bench import judge, summarize, table


def press(x, y, button=1):
    return {"type": "press", "button": button, "x": x, "y": y}


def release(x, y, button=1):
    return {"type": "release", "button": button, "x": x, "y": y}


def key(keycode, state=0, char=""):
    return {"type": "key", "keycode": keycode, "state": state, "char": char}


def wheel(notches):
    return {"type": "scroll", "notches": notches}


CLICK = {"button": 1, "presses": 1, "x": 100, "y": 50}
SHIFT, CTRL = 1, 4  # Gdk.ModifierType values


def test_click_that_arrives_where_it_was_sent_is_ok():
    assert judge("click", CLICK, [press(100.4, 50), release(100.4, 50)]) == ("ok", "")


def test_click_with_no_events_or_no_release_is_missing():
    assert judge("click", CLICK, [])[0] == "missing"
    assert judge("click", CLICK, [press(100, 50)])[0] == "missing"


def test_click_that_arrives_twice_is_extra():
    events = [press(100, 50), release(100, 50), press(100, 50), release(100, 50)]
    assert judge("click", CLICK, events)[0] == "extra"


def test_click_in_the_wrong_place_or_with_the_wrong_button_is_wrong():
    assert judge("click", CLICK, [press(103, 50), release(103, 50)])[0] == "wrong"
    assert judge("click", CLICK, [press(100, 50, 3), release(100, 50, 3)])[0] == "wrong"


def test_double_click_needs_both_presses():
    double = CLICK | {"presses": 2}
    assert judge("double", double, [press(100, 50), release(100, 50)])[0] == "missing"
    assert judge("double", double, [press(100, 50), release(100, 50)] * 2)[0] == "ok"


def test_drag_must_press_at_the_start_and_release_at_the_end():
    drag = {"x": 10, "y": 10, "x2": 200, "y2": 90}
    assert judge("drag", drag, [press(10, 10), release(200, 90)])[0] == "ok"
    assert judge("drag", drag, [press(10, 10)])[0] == "missing"
    assert judge("drag", drag, [press(10, 10), release(10, 10)])[0] == "wrong"
    assert judge("drag", drag, [press(60, 10), release(200, 90)])[0] == "wrong"


def test_key_must_match_keycode_and_modifiers():
    want = {"keycodes": {38}, "mods": CTRL}
    assert judge("key", want, [key(38, CTRL)])[0] == "ok"
    assert judge("key", want, [key(38, CTRL | 16)])[0] == "ok"  # NumLock and friends are ignored
    assert judge("key", want, [])[0] == "missing"
    assert judge("key", want, [key(39, CTRL)])[0] == "wrong"
    assert judge("key", want, [key(38, 0)])[0] == "wrong"
    assert judge("key", want, [key(38, CTRL | SHIFT)])[0] == "wrong"
    assert judge("key", want, [key(38, CTRL), key(38, CTRL)])[0] == "extra"


def test_typed_text_must_arrive_complete_and_in_order():
    want = {"text": "aB1!"}
    assert judge("type", want, [key(0, char=c) for c in "aB1!"])[0] == "ok"
    assert judge("type", want, [])[0] == "missing"
    assert judge("type", want, [key(0, char=c) for c in "aB1"])[0] == "missing"
    assert judge("type", want, [key(0, char=c) for c in "aB1!!"])[0] == "extra"
    assert judge("type", want, [key(0, char=c) for c in "Ba1!"])[0] == "wrong"


def test_scroll_counts_wheel_clicks_with_direction():
    assert judge("scroll", {"notches": -3}, [wheel(-1)] * 3)[0] == "ok"
    assert judge("scroll", {"notches": 3}, [wheel(3.0)])[0] == "ok"
    assert judge("scroll", {"notches": 3}, [])[0] == "missing"
    assert judge("scroll", {"notches": 3}, [wheel(1)] * 2) == ("missing", "2 of 3 notches")
    assert judge("scroll", {"notches": 3}, [wheel(1)] * 4)[0] == "extra"
    assert judge("scroll", {"notches": 3}, [wheel(-1)] * 3)[0] == "wrong"


def test_summary_counts_each_verdict_and_keeps_a_bounded_sample_of_failures():
    results = [{"state": "focused", "kind": "click", "verdict": "ok", "detail": "", "secs": 0.3, "focused_after": True}] * 97
    results += [{"state": "focused", "kind": "click", "verdict": "missing", "detail": f"lost {i}", "secs": 0.9, "focused_after": False} for i in range(3)]
    results += [{"state": "unfocused", "kind": "key", "verdict": "refused", "detail": "no window", "secs": 0.01}] * 30
    click, keys = summarize(results)
    assert (click["sent"], click["ok"], click["missing"], click["not_focused_after"]) == (100, 97, 3, 3)
    assert click["samples"] == ["lost 0", "lost 1", "lost 2"]
    assert click["median_secs"] == 0.3
    assert (keys["sent"], keys["refused"], len(keys["samples"])) == (30, 30, 10)
    text = table([click, keys])
    assert "focused    click     100    97        3" in text
    assert "e.g. lost 0" in text
