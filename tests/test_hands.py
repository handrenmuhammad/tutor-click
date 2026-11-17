"""The guards and key parsing in tutor_click.hands, against a fake hyprctl."""
import json
import re
from types import SimpleNamespace

import pytest

from tutor_click.platform import hands as base_hands
from tutor_click.platform.base import Monitor, Refused
from tutor_click.platform.hyprland import hands, wheel
from tutor_click.platform.hyprland.hands import Hands, parse_combo, parse_mods


def client(address, cls, at, size, workspace=1, floating=False, focus=0, **over) -> dict:
    return {
        "address": address, "class": cls, "title": cls, "at": list(at), "size": list(size),
        "workspace": {"id": workspace}, "floating": floating, "focusHistoryID": focus,
        "mapped": True, "hidden": False,
    } | over


class FakeHyprctl:
    """Stands in for the subprocess module inside the Hyprland hands. Records every Lua eval.

    Like Hyprland, it remembers which window was focused last and where the cursor was sent.
    """

    def __init__(self, clients, monitor="HDMI-A-1", workspace=1, special=0, active=None):
        self.clients = clients
        self.monitors = [{"name": monitor, "activeWorkspace": {"id": workspace}, "specialWorkspace": {"id": special}}]
        self.active = active
        self.cursor = {"x": 0, "y": 0}
        self.reply = "ok"
        self.reject = None  # substring of an eval that Hyprland refuses
        self.focus_works = True
        self.nudge = (0, 0)  # the user's hand on the mouse: added to every cursor move
        self.evals: list[str] = []

    def check_output(self, cmd):
        assert cmd[0] == "hyprctl" and cmd[2] == "-j"
        return json.dumps({
            "clients": self.clients, "monitors": self.monitors, "activewindow": self.active or {}, "cursorpos": self.cursor,
        }[cmd[1]])

    def run(self, cmd, **_):
        assert cmd[:2] == ["hyprctl", "eval"]
        code = cmd[2]
        self.evals.append(code)
        if self.reply != "ok" or (self.reject and self.reject in code):
            return SimpleNamespace(stdout="error: rejected\n")
        if self.focus_works and (m := re.search(r'focus\(\{window="address:(\w+)"', code)):
            self.active = next(c for c in self.clients if c["address"] == m[1])
        if m := re.search(r"cursor\.move\(\{x=(-?\d+),y=(-?\d+)\}", code):
            self.cursor = {"x": int(m[1]) + self.nudge[0], "y": int(m[2]) + self.nudge[1]}
        return SimpleNamespace(stdout="ok\n")


PINTA = client("0xaa", "pinta", (0, 0), (1000, 800))
KITTY = client("0xbb", "kitty", (1000, 0), (500, 800), focus=1)
NAUTILUS = client("0xcc", "org.gnome.Nautilus", (1500, 0), (500, 800), focus=2)


@pytest.fixture
def hypr(monkeypatch):
    fake = FakeHyprctl([PINTA, KITTY, NAUTILUS])
    monkeypatch.setattr(hands, "subprocess", fake)
    monkeypatch.setattr(base_hands, "time", SimpleNamespace(sleep=lambda _: None))
    return fake


@pytest.fixture
def wheel_turns(monkeypatch):
    turns = []
    monkeypatch.setattr(wheel, "scroll", turns.append)
    return turns


def make(mon=None) -> Hands:
    return Hands(mon or Monitor("HDMI-A-1", 3440, 1440, 1.0))


# ---- clicks ---------------------------------------------------------------
def test_click_focuses_moves_presses_and_releases_in_order(hypr):
    assert make().click(100, 200).cls == "pinta"
    assert hypr.evals == [
        'hl.dispatch(hl.dsp.focus({window="address:0xaa"}))',
        "hl.dispatch(hl.dsp.cursor.move({x=100,y=200}))",
        'hl.dispatch(hl.dsp.send_key_state({mods="",key="mouse:272",state="down"}))',
        'hl.dispatch(hl.dsp.send_key_state({mods="",key="mouse:272",state="up"}))',
        "hl.dispatch(hl.dsp.cursor.move({x=101,y=200}))",
        "hl.dispatch(hl.dsp.cursor.move({x=100,y=200}))",
    ]


def test_double_click_presses_twice_and_right_click_uses_the_right_button(hypr):
    make().click(100, 200, count=2)
    assert sum('state="down"' in e for e in hypr.evals) == 2
    hypr.evals.clear()
    make().click(100, 200, button="right", mods="SHIFT")
    assert 'send_key_state({mods="SHIFT",key="mouse:273",state="down"})' in hypr.evals[2]


def test_monitor_offset_is_added_including_negative_origins(hypr):
    hypr.clients = [client("0xaa", "pinta", (-1920, 0), (1920, 1080))]
    hypr.monitors[0]["name"] = "DP-1"
    make(Monitor("DP-1", 1920, 1080, 1.0, x=-1920, y=0)).click(100.4, 200.6)
    assert "cursor.move({x=-1820,y=201})" in hypr.evals[1]


def test_click_on_empty_desktop_is_refused_and_sends_nothing(hypr):
    with pytest.raises(Refused, match="no app window"):
        make().click(3000, 1200)
    assert hypr.evals == []


def test_terminals_are_never_acted_in(hypr):
    with pytest.raises(Refused, match="never click or type in kitty"):
        make().click(1100, 100)
    assert hypr.evals == []


def test_lesson_stays_in_the_first_app_until_reset(hypr):
    h = make()
    h.click(100, 100)
    assert h.target == "pinta"
    with pytest.raises(Refused, match="outside pinta"):
        h.click(1600, 100)
    h.reset()
    assert h.click(1600, 100).cls == "org.gnome.Nautilus"


def test_window_on_another_workspace_is_not_there(hypr):
    hypr.clients = [client("0xaa", "pinta", (0, 0), (1000, 800), workspace=2)]
    with pytest.raises(Refused, match="no app window"):
        make().click(100, 100)


def test_floating_window_wins_over_tiled_and_special_workspace_over_both(hypr):
    floating = client("0xdd", "org.gnome.Calculator", (50, 50), (300, 300), floating=True, focus=5)
    hypr.clients = [PINTA, floating]
    assert make().window_at(100, 100).cls == "org.gnome.Calculator"
    scratch = client("0xee", "scratchpad", (0, 0), (500, 500), workspace=-98, focus=9)
    hypr.clients.append(scratch)
    hypr.monitors[0]["specialWorkspace"] = {"id": -98}
    assert make().window_at(100, 100).cls == "scratchpad"


def test_hyprland_rejecting_the_input_is_a_refusal(hypr):
    hypr.reply = "error: no such dispatcher"
    with pytest.raises(Refused, match="Hyprland rejected") as e:
        make().click(100, 100)
    assert e.value.code == "INPUT_REJECTED"


# ---- checks before any input goes out ----------------------------------------
def pressed(hypr) -> bool:
    return any("send_key_state" in e or "send_shortcut" in e for e in hypr.evals)


def test_nothing_is_pressed_when_the_window_does_not_take_focus(hypr):
    hypr.focus_works = False
    hypr.active = KITTY
    with pytest.raises(Refused) as e:
        make().click(100, 100)
    assert e.value.code == "WINDOW_NOT_FOCUSED"
    assert not pressed(hypr)


def test_nothing_is_pressed_when_the_cursor_is_not_where_it_was_sent(hypr, wheel_turns):
    hypr.nudge = (160, 5)  # the stray press the benchmark saw: 164 px off
    for act in (lambda h: h.click(100, 100), lambda h: h.drag(100, 100, 300, 300),
                lambda h: h.scroll(100, 100, 2), lambda h: h.keys("g", hover=(100, 100))):
        with pytest.raises(Refused) as e:
            act(make())
        assert e.value.code == "CURSOR_NOT_AT_TARGET"
    assert not pressed(hypr)
    assert wheel_turns == []


def test_two_pixels_of_cursor_drift_are_tolerated(hypr):
    hypr.nudge = (2, -2)
    make().click(100, 100)
    assert pressed(hypr)


def test_armed_runs_after_the_cursor_is_placed_and_before_the_press(hypr, wheel_turns):
    for act in (lambda h, a: h.click(100, 100, armed=a), lambda h, a: h.drag(100, 100, 300, 300, armed=a),
                lambda h, a: h.scroll(100, 100, 2, armed=a), lambda h, a: h.keys("g", hover=(100, 100), armed=a),
                lambda h, a: h.keys("g", armed=a), lambda h, a: h.type_text("hi", armed=a)):
        hypr.evals.clear()
        hypr.active = PINTA
        seen = []
        act(make(), lambda seen=seen: seen.append(list(hypr.evals)))
        assert len(seen) == 1
        assert not any("send_key_state" in e or "send_shortcut" in e for e in seen[0])
    assert wheel_turns == [2]


def test_refusals_carry_a_reason_code(hypr):
    cases = [(lambda h: h.click(3000, 1200), "NO_WINDOW"), (lambda h: h.click(1100, 100), "NEVER_ACT"),
             (lambda h: h.drag(100, 100, 1200, 100), "OUTSIDE_APP"), (lambda h: h.keys("hyper+a"), "UNKNOWN_KEY"),
             (lambda h: h.type_text("x" * 500), "TOO_LONG")]
    for act, code in cases:
        with pytest.raises(Refused) as e:
            act(make())
        assert e.value.code == code


def test_bad_window_address_is_refused(hypr):
    hypr.clients = [client('0xaa"})) os.exit(', "pinta", (0, 0), (1000, 800))]
    with pytest.raises(Refused, match="could not identify"):
        make().click(100, 100)
    assert hypr.evals == []


# ---- drag and scroll --------------------------------------------------------
def test_drag_presses_at_the_start_and_releases_at_the_end(hypr):
    make().drag(100, 100, 400, 300)
    down = next(i for i, e in enumerate(hypr.evals) if 'state="down"' in e)
    up = next(i for i, e in enumerate(hypr.evals) if 'state="up"' in e)
    assert "cursor.move({x=100,y=100})" in hypr.evals[down - 1]
    assert "cursor.move({x=400,y=300})" in hypr.evals[up - 1]
    assert up - down == 13  # 12 moves in between


def test_drag_releases_the_button_even_if_a_move_is_rejected_halfway(hypr):
    hypr.reject = "cursor.move({x=250,y=200})"  # the sixth of twelve moves
    with pytest.raises(Refused):
        make().drag(100, 100, 400, 300)
    assert 'state="down"' in hypr.evals[-8]
    assert 'state="up"' in hypr.evals[-1]


def test_drag_that_ends_outside_the_window_is_refused(hypr):
    with pytest.raises(Refused, match="outside the app window"):
        make().drag(100, 100, 1200, 100)
    assert hypr.evals == []


def test_scroll_moves_the_cursor_then_turns_the_wheel(hypr, wheel_turns):
    assert make().scroll(100, 100, -3).cls == "pinta"
    assert hypr.evals == ["hl.dispatch(hl.dsp.cursor.move({x=100,y=100}))"]
    assert wheel_turns == [-3]


def test_wheel_failure_is_a_refusal(hypr, monkeypatch):
    def broken(_):
        raise wheel.WheelError("no virtual pointer")

    monkeypatch.setattr(wheel, "scroll", broken)
    with pytest.raises(Refused, match="could not scroll"):
        make().scroll(100, 100, 2)


# ---- keys and typing ---------------------------------------------------------
def test_keys_go_to_the_window_last_acted_in(hypr):
    h = make()
    h.click(100, 100)
    hypr.evals.clear()
    h.keys("ctrl+z g")
    assert hypr.evals == [
        'hl.dispatch(hl.dsp.send_shortcut({mods="CTRL",key="Z",window="address:0xaa"}))',
        'hl.dispatch(hl.dsp.send_shortcut({mods="",key="G",window="address:0xaa"}))',
    ]


def test_keys_without_a_point_use_the_active_window(hypr):
    hypr.active = PINTA
    assert make().keys("tab").cls == "pinta"


def test_keys_with_no_usable_window_are_refused(hypr):
    hypr.active = KITTY
    with pytest.raises(Refused, match="never click or type in kitty"):
        make().keys("enter")
    hypr.active = None
    with pytest.raises(Refused, match="no app window"):
        make().keys("enter")


def test_too_many_keys_in_one_step_is_refused(hypr):
    hypr.active = PINTA
    with pytest.raises(Refused, match="too many keys"):
        make().keys(" ".join("a" * (hands.MAX_KEYS + 1)))
    assert hypr.evals == []


def test_typing_is_validated_before_any_key_is_sent(hypr):
    hypr.active = PINTA
    with pytest.raises(Refused, match="can't type the character"):
        make().type_text("café")
    with pytest.raises(Refused, match="too long"):
        make().type_text("x" * (hands.MAX_TYPE_CHARS + 1))
    assert hypr.evals == []


def test_typing_is_sent_in_batches_of_twenty_keys(hypr):
    hypr.active = PINTA
    make().type_text("a" * 45)
    assert [e.count("send_shortcut") for e in hypr.evals] == [20, 20, 5]


def test_typed_characters_map_to_us_layout_keys(hypr):
    hypr.active = PINTA
    make().type_text("aB 1!_")
    sent = hypr.evals[0]
    for expected in ('mods="",key="A"', 'mods="SHIFT",key="B"', 'mods="",key="space"', 'mods="",key="1"',
                     'mods="SHIFT",key="1"', 'mods="SHIFT",key="minus"'):
        assert expected in sent


@pytest.mark.parametrize(("combo", "expected"), [
    ("tab", ("", "Tab")),
    ("ctrl+shift+a", ("CTRL SHIFT", "A")),
    ("Ctrl+Z", ("CTRL", "Z")),
    ("numpad1", ("", "code:87")),
    ("f2", ("", "F2")),
    ("ctrl++", ("CTRL SHIFT", "equal")),
    ("?", ("SHIFT", "slash")),
    ("win+e", ("SUPER", "E")),
])
def test_parse_combo(combo, expected):
    assert parse_combo(combo) == expected


@pytest.mark.parametrize("combo", ["hyper+a", "ctrl+notakey", "ctrl+é"])
def test_parse_combo_refuses_what_it_does_not_know(combo):
    with pytest.raises(Refused):
        parse_combo(combo)


def test_parse_mods():
    assert parse_mods("") == ""
    assert parse_mods("ctrl+shift") == "CTRL SHIFT"
    assert parse_mods("shift ctrl shift") == "SHIFT CTRL"
    with pytest.raises(Refused, match="modifier a"):
        parse_mods("ctrl+a")


def test_key_names_cannot_carry_lua(hypr):
    with pytest.raises(Refused):
        hands._key_call("", 'A"}) os.exit(')
    with pytest.raises(Refused):
        hands._key_call('CTRL"', "A")
