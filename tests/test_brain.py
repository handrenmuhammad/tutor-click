"""What the model answers is checked and clamped before anything acts on it."""
import json

import pytest

from tutor_click import config
from tutor_click.brain import SCHEMA, SYSTEM, Step, Tutor, check_answer
from tutor_click.platform.base import Shot
from tutor_click.providers import ProviderError

SHOT = Shot(b"jpeg", 1568, 656, 0.4558)


def answer(**over) -> dict:
    base = {
        "say": "Click the brush.", "caption": "Pick the brush", "point": True, "x": 100, "y": 100,
        "action": "click", "x2": 0, "y2": 0, "keys": "", "text": "", "notches": 0,
        "expect": "The brush button is highlighted.", "repeat_safe": True, "concept": "Paintbrush tool", "risk": "read_only",
        "wait_user": False, "done": False,
    }
    return base | over


class FakeProvider:
    """Hands back canned answers and remembers what it was sent."""

    name, model = "Fake", "fake-1"

    def __init__(self, *answers, id="fake"):
        self.id = id
        self.answers = list(answers)
        self.sent = []  # (system, schema, jpeg, prompt)
        self.aborted = False

    def ask(self, system, schema, jpeg, prompt):
        self.sent.append((system, schema, jpeg, prompt))
        return self.answers.pop(0)

    def abort(self):
        self.aborted = True


def FakeTutor(*answers) -> Tutor:
    return Tutor(FakeProvider(*answers))


def step(**over) -> Step:
    return FakeTutor(answer(**over)).step(SHOT, "pinta", "teach me", "guide")


def test_valid_click_is_kept():
    s = step()
    assert (s.action, s.point, s.done, s.wait_user) == ("click", (100, 100), False, False)


def test_point_outside_the_screenshot_is_dropped_and_the_click_with_it():
    for x, y in ((1568, 100), (100, 656), (-1, 5), (99999, 99999)):
        s = step(x=x, y=y)
        assert s.point is None
        assert s.action == "none"


def test_point_false_means_no_point_even_with_coordinates():
    s = step(point=False)
    assert s.point is None
    assert s.action == "none"


def test_key_action_survives_without_a_point():
    s = step(point=False, action="key", keys=" ctrl+z ")
    assert (s.action, s.keys, s.point) == ("key", "ctrl+z", None)


def test_unknown_action_becomes_none():
    assert step(action="rm -rf").action == "none"


def test_scroll_is_clamped_to_ten_notches():
    assert step(action="scroll", notches=500).notches == 10
    assert step(action="scroll", notches=-500).notches == -10


def test_scroll_of_zero_notches_is_not_an_action():
    assert step(action="scroll", notches=0).action == "none"


def test_drag_needs_an_end_point_inside_the_screenshot():
    assert step(action="drag", x2=5000, y2=10).action == "none"
    s = step(action="drag", x2=300, y2=200)
    assert (s.action, s.point2) == ("drag", (300, 200))


def test_risk_repeat_safety_and_expectation_are_carried_on_the_step():
    s = step(risk="sensitive", repeat_safe=False, expect=" The Save dialog opens. ")
    assert (s.risk, s.repeat_safe, s.expect) == ("sensitive", False, "The Save dialog opens.")


def test_an_unknown_risk_label_is_treated_as_sensitive():
    assert step(risk="harmless").risk == "sensitive"


def test_every_schema_field_is_required_and_no_extras_are_allowed():
    assert sorted(SCHEMA["required"]) == sorted(SCHEMA["properties"])
    assert SCHEMA["additionalProperties"] is False


def test_history_and_asks_are_bounded():
    n = config.HISTORY_TURNS + config.ASKS_KEPT + 5
    t = FakeTutor(*[answer() for _ in range(n)])
    for i in range(n):
        t.step(SHOT, "pinta", f"ask {i}", "guide")
    assert len(t.history) == config.HISTORY_TURNS
    assert t.asks == [f"ask {i}" for i in range(n - config.ASKS_KEPT, n)]


def test_automatic_events_are_not_recorded_as_the_users_requests():
    t = FakeTutor(answer(), answer())
    t.step(SHOT, "pinta", "draw a circle", "auto")
    t.step(SHOT, "pinta", "(automatic) clicked", "auto", from_user=False)
    assert t.asks == ["draw a circle"]


def test_prompt_carries_size_window_mode_goal_and_event():
    t = FakeTutor(answer(), answer())
    t.step(SHOT, 'pinta - "Unsaved Image 1"', "draw a circle", "guide")
    t.step(SHOT, 'pinta - "Unsaved Image 1"', "I did that. What's next?", "guide")
    system, schema, jpeg, prompt = t.provider.sent[1]
    assert (system, schema, jpeg) == (SYSTEM, SCHEMA, b"jpeg")
    assert "Screenshot size: 1568x656 px." in prompt
    assert 'Focused window: pinta - "Unsaved Image 1"' in prompt
    assert "Mode: guide" in prompt
    assert "- draw a circle" in prompt
    assert "Then: draw a circle" in prompt  # the first exchange is replayed
    assert prompt.endswith("Now: I did that. What's next?")


# ---- providers ---------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    None, "click", [], {},
    {k: v for k, v in answer().items() if k != "risk"},  # a field missing
    answer() | {"extra": 1},
    answer(x="100"), answer(x=True), answer(x=1.5), answer(point="yes"), answer(say=None), answer(notches=None),
])
def test_an_answer_that_does_not_match_the_schema_is_rejected_before_anything_uses_it(bad):
    t = FakeTutor(bad)
    with pytest.raises(ProviderError) as e:
        t.step(SHOT, "pinta", "teach me", "guide")
    assert e.value.code == "invalid_answer"
    assert t.history == []  # nothing from a bad answer is remembered


def test_check_answer_accepts_a_well_formed_answer():
    assert check_answer(answer()) == answer()


def test_the_lesson_moves_to_another_provider_with_its_history():
    first, second = FakeProvider(answer(caption="Pick the brush"), id="claude-code"), FakeProvider(answer(), id="codex")
    t = Tutor(first)
    t.step(SHOT, "pinta", "paint a line", "auto")
    t.provider = second
    t.step(SHOT, "pinta", "(automatic) Input sent: click at (100, 100).", "auto", from_user=False)
    assert len(first.sent) == 1
    prompt = second.sent[0][3]
    assert "- paint a line" in prompt  # the goal
    assert "Then: paint a line" in prompt and "[caption: Pick the brush] [action: click at (100, 100)]" in prompt  # what was already done
    assert second.sent[0][:2] == first.sent[0][:2]  # same instructions and schema for every provider


def test_abort_reaches_the_provider():
    t = FakeTutor()
    t.abort()
    assert t.provider.aborted


def test_reset_forgets_the_lesson():
    t = FakeTutor(answer())
    t.step(SHOT, "pinta", "draw a circle", "guide")
    t.reset()
    assert (t.history, t.asks, t.concepts) == ([], [], [])


def test_concepts_are_collected_once_named_in_the_prompt_and_bounded():
    n = config.CONCEPTS_KEPT + 3
    answers = [answer(concept=" Paintbrush tool "), answer(concept="Paintbrush tool"), answer(concept="")]
    t = FakeTutor(*answers, *[answer(concept=f"idea {i}") for i in range(n)])
    assert "Already taught in this lesson: (nothing yet)" in (t.step(SHOT, "pinta", "teach me", "guide"), t.provider.sent[0][3])[1]
    t.step(SHOT, "pinta", "next", "guide", from_user=False)
    t.step(SHOT, "pinta", "next", "guide", from_user=False)
    assert t.concepts == ["Paintbrush tool"]
    t.step(SHOT, "pinta", "next", "guide", from_user=False)
    assert "Already taught in this lesson: Paintbrush tool\n" in t.provider.sent[3][3]
    for _ in range(n - 1):
        t.step(SHOT, "pinta", "next", "guide", from_user=False)
    assert t.concepts == [f"idea {i}" for i in range(n - config.CONCEPTS_KEPT, n)]


def test_a_restored_tutor_sends_the_same_prompt_as_the_one_that_was_saved():
    a = FakeTutor(answer(), answer(concept="Layers"), answer())
    a.step(SHOT, "pinta", "draw a circle", "guide")
    a.step(SHOT, "pinta", "I did that. What's next?", "guide", from_user=False)
    memory = json.loads(json.dumps(a.memory()))  # as it comes back from a lesson record
    b = FakeTutor(answer())
    b.restore(memory)
    a.step(SHOT, "pinta", "and now?", "guide")
    b.step(SHOT, "pinta", "and now?", "guide")
    assert b.provider.sent[0][3] == a.provider.sent[2][3]
    assert "Layers" in b.provider.sent[0][3]


def test_describe():
    assert Step("", "", (10, 20), False, "click").describe() == "click at (10, 20)"
    assert Step("", "", (10, 20), False, "right_click", keys="shift").describe() == "right click at (10, 20) holding shift"
    assert Step("", "", None, False, "key", keys="ctrl+z").describe() == "pressed ctrl+z"
    assert Step("", "", None, False, "type", text="hi").describe() == "typed 'hi'"
    assert Step("", "", (1, 2), False, "scroll", notches=-3).describe() == "scrolled up 3 notches at (1, 2)"
    assert Step("", "", (1, 2), False, "drag", point2=(3, 4)).describe() == "dragged from (1, 2) to (3, 4)"
