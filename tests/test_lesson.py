"""The lesson loop in tutor_click.daemon, with a fake tutor, fake hands, fake screen and a fake clock."""
import json
import time

import pytest
from images import jpeg

from tutor_click import config, consent, daemon, providers, screen
from tutor_click.brain import Step
from tutor_click.hands import Refused, Window
from tutor_click.providers import ProviderError
from tutor_click.screen import Monitor, Shot

W, H, FACTOR = 1720, 720, 0.5  # screenshot px = logical px * 0.5
PINTA = Window("0xaa", "pinta", "Unsaved Image 1", (0, 0), (3440, 1440))


class FakeClock:
    """time.sleep advances the clock instead of waiting."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, secs):
        self.now += secs

    strftime = staticmethod(time.strftime)


class FakeScreen:
    """Every capture shows something new, unless frozen or given a script of frame numbers."""

    def __init__(self):
        self.n = 0
        self.frozen = False
        self.script: list[int] = []
        self.captures = 0

    def capture(self, mon, max_edge):
        self.captures += 1
        if self.script:
            self.n = self.script.pop(0)
        elif not self.frozen:
            self.n += 1
        return Shot(jpeg(W, H, ((40 * (self.n % 40), 300, 30),)), W, H, FACTOR)


class FakeUI:
    """Overlay or panel: remembers every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args))


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)

    def stop(self):
        pass


def fake_provider(pid, name, billing="subscription", sends_to="Anthropic", paid_by="your Claude subscription", problem=None):
    """A provider class as the daemon sees one: who it is, who pays, whether it can be used."""

    class P:
        id, menu, model = pid, name, ""

        def health(self):
            return problem

        def abort(self):
            self.aborted = True

    P.name, P.billing, P.sends_to, P.paid_by = name, billing, sends_to, paid_by
    return P


FAKES = {
    "claude-code": fake_provider("claude-code", "Claude"),
    "codex": fake_provider("codex", "Codex", sends_to="OpenAI", paid_by="your ChatGPT plan"),
    "local": fake_provider("local", "the local model", billing="local", sends_to="this computer only", paid_by="nobody"),
    "api": fake_provider("api", "the Claude API", billing="api_key", paid_by="your Anthropic API key, billed per use", problem="no ANTHROPIC_API_KEY is set"),
    "openai": fake_provider("openai", "the OpenAI API", billing="api_key", sends_to="OpenAI", paid_by="your OpenAI API key, billed per use"),
}


class FakeTutor:
    """Hands out prepared steps and remembers what it was asked, and by which provider."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.asked = []  # (event, mode, from_user)
        self.asked_of = []  # provider id for each ask
        self.on_step = None
        self.provider = FAKES["claude-code"]()

    def step(self, shot, window, event, mode, from_user=True):
        self.asked.append((event, mode, from_user))
        self.asked_of.append(self.provider.id)
        if self.on_step:
            self.on_step()
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def abort(self):
        self.provider.abort()

    def reset(self):
        pass


class FakeHands:
    """Records each action. Like the real hands, it calls armed() just before the input goes out."""

    def __init__(self, refuse=None, code="REFUSED"):
        self.calls = []
        self.refuse, self.code = refuse, code
        self.target = None

    def _did(self, armed, *call):
        if self.refuse:
            raise Refused(self.refuse, self.code)
        if armed:
            armed()
        self.calls.append(call)
        return PINTA

    def click(self, x, y, button="left", count=1, mods="", armed=None):
        return self._did(armed, "click", x, y, button, count, mods)

    def drag(self, x, y, x2, y2, mods="", armed=None):
        return self._did(armed, "drag", x, y, x2, y2, mods)

    def scroll(self, x, y, notches, armed=None):
        return self._did(armed, "scroll", x, y, notches)

    def keys(self, sequence, hover=None, armed=None):
        return self._did(armed, "keys", sequence, hover)

    def type_text(self, text, armed=None):
        return self._did(armed, "type", text)

    def window_at(self, x, y):
        return PINTA

    def reset(self):
        self.target = None


def click(x=200, y=100, action="click", **over) -> Step:
    return Step(say=f"Click at {x}.", caption="Click", point=(x, y), done=False, action=action, **over)


TALK = Step(say="This is the canvas.", caption="The canvas", point=None, done=False)
DONE = Step(say="All done.", caption="Done", point=None, done=True)


@pytest.fixture
def make(monkeypatch):
    def build(steps, mode="guide", hands=None):
        tutor = FakeTutor(steps)
        monkeypatch.setattr(daemon, "Tutor", lambda: tutor)
        for name in ("Recorder", "Transcriber"):
            monkeypatch.setattr(daemon, name, lambda: None)
        monkeypatch.setattr(daemon, "Speaker", FakeSpeaker)
        monkeypatch.setattr(daemon, "time", FakeClock())
        monkeypatch.setattr(providers, "PROVIDERS", FAKES)
        d = daemon.Daemon()
        d.screen = FakeScreen()
        monkeypatch.setattr(screen, "capture", d.screen.capture)
        monkeypatch.setattr(screen, "active_window", lambda: 'pinta - "Unsaved Image 1"')
        d.overlay, d.panel = FakeUI(), FakeUI()
        d.hands = hands or FakeHands()
        d.monitor = Monitor("HDMI-A-1", 3440, 1440, 1.0)
        d.mode = mode
        return d

    return build


def trace_lines() -> list[dict]:
    if not config.TRACE_FILE.exists():
        return []
    return [json.loads(line) for line in config.TRACE_FILE.read_text().splitlines()]


def test_guide_mode_explains_and_never_acts(make):
    d = make([click()])
    d._lesson(d.job_id, "teach me the brush")
    assert d.hands.calls == []
    assert d.speaker.said == ["Click at 200."]
    assert ("show_step", ((400.0, 200.0), "Click", "Click at 200.")) in d.overlay.calls
    assert ("set_can_do", (True,)) in d.panel.calls


def test_auto_mode_acts_then_looks_again_until_done(make):
    d = make([click(200, 100), click(300, 100), DONE], mode="auto")
    d._lesson(d.job_id, "draw a line")
    assert d.hands.calls == [("click", 400.0, 200.0, "left", 1, ""), ("click", 600.0, 200.0, "left", 1, "")]
    events = [(e.split(":")[0], mode, from_user) for e, mode, from_user in d.tutor.asked]
    assert events == [("draw a line", "auto", True), ("(automatic) Input sent", "auto", False), ("(automatic) Input sent", "auto", False)]
    assert d.speaker.said == ["Click at 200.", "Click at 300.", "All done."]


def test_every_kind_of_action_reaches_the_hands_in_logical_pixels(make):
    steps = [
        click(action="double_click"),
        click(action="right_click", keys="shift"),
        Step("", "", (200, 100), False, "drag", point2=(400, 300), keys="ctrl"),
        Step("", "", (200, 100), False, "scroll", notches=-3),
        Step("", "", (200, 100), False, "key", keys="ctrl+z"),
        Step("", "", None, False, "type", text="hello"),
        DONE,
    ]
    d = make(steps, mode="auto")
    d._lesson(d.job_id, "show me everything")
    assert d.hands.calls == [
        ("click", 400.0, 200.0, "left", 2, ""),
        ("click", 400.0, 200.0, "right", 1, "SHIFT"),
        ("drag", 400.0, 200.0, 800.0, 600.0, "CTRL"),
        ("scroll", 400.0, 200.0, -3),
        ("keys", "ctrl+z", (400.0, 200.0)),
        ("type", "hello"),
    ]


def test_same_action_three_times_stops_after_two_attempts(make):
    d = make([click(), click(), click(), click()], mode="auto")
    d._lesson(d.job_id, "press the button")
    assert len(d.hands.calls) == 2
    assert len(d.tutor.asked) == 3
    assert "three times" in d.speaker.said[-1]


def test_auto_mode_pauses_after_the_step_limit(make):
    n = config.AUTO_MAX_STEPS
    d = make([click(x=10 + i) for i in range(n + 3)], mode="auto")
    d._lesson(d.job_id, "keep going")
    assert len(d.hands.calls) == n
    assert f"{n} steps in a row" in d.speaker.said[-1]


def test_refused_action_stops_the_lesson_and_says_why(make):
    d = make([click(), click(300)], mode="auto", hands=FakeHands(refuse="I never click or type in kitty.", code="NEVER_ACT"))
    d._lesson(d.job_id, "do it")
    assert len(d.tutor.asked) == 1
    assert d.speaker.said[-1] == "I never click or type in kitty."


def test_wait_user_and_done_are_not_performed_in_auto_mode(make):
    d = make([click(wait_user=True)], mode="auto")
    d._lesson(d.job_id, "delete the layer")
    assert d.hands.calls == []
    d = make([Step("Finished.", "Done", (1, 1), True, "click")], mode="auto")
    d._lesson(d.job_id, "anything else?")
    assert d.hands.calls == []


def test_explain_never_acts_even_in_auto_mode(make):
    d = make([click()], mode="auto")
    d._lesson(d.job_id, daemon.EXPLAIN, True, "never")
    assert d.hands.calls == []
    assert d.speaker.said == ["Click at 200."]


def test_interrupt_while_thinking_cancels_before_anything_is_shown_or_done(make):
    d = make([click()], mode="auto")
    d.tutor.on_step = d._cancel  # the user talks while Claude is answering
    with pytest.raises(daemon.Cancelled):
        d._lesson(0, "draw a line")
    assert d.hands.calls == []
    assert d.speaker.said == []
    assert not any(name == "show_step" for name, _ in d.overlay.calls)


def test_do_it_for_me_performs_the_step_on_screen_without_asking_again(make):
    d = make([click(), TALK])
    d._lesson(d.job_id, "how do I pick the brush?")
    assert d.hands.calls == []
    d._do(d.job_id)
    assert d.hands.calls == [("click", 400.0, 200.0, "left", 1, "")]
    assert [e.split(":")[0] for e, _, _ in d.tutor.asked] == [
        "how do I pick the brush?", "(automatic) At the user's request. Input sent",
    ]


def test_do_it_for_me_looks_again_when_the_step_is_stale(make):
    d = make([click(), click(300), TALK])
    d._lesson(d.job_id, "how do I pick the brush?")
    daemon.time.sleep(daemon.FRESH_SECS + 1)
    d._do(d.job_id)
    assert d.tutor.asked[1] == (daemon.DO_IT, "auto", True)
    assert d.hands.calls == [("click", 600.0, 200.0, "left", 1, "")]


# ---- what an action did --------------------------------------------------------
def test_a_window_that_changed_after_the_input_is_reported_as_measured_not_as_success(make):
    d = make([click(expect="The brush button is highlighted."), DONE], mode="auto")
    d._lesson(d.job_id, "pick the brush")
    event = d.tutor.asked[1][0]
    assert "Input sent: click at (200, 100)." in event
    assert "The cursor was on target when it was sent." in event
    assert "the pinta window changed afterwards" in event
    assert 'You expected: "The brush button is highlighted."' in event
    assert "Judge from it whether the step worked" in event
    assert "performed" not in event and "worked." not in event
    assert [r["status"] for r in trace_lines() if r["ev"] == "outcome"] == ["verified"]


def test_no_visible_change_stops_an_action_that_is_not_safe_to_repeat(make):
    d = make([click(repeat_safe=False), click(), TALK], mode="auto")
    d.screen.frozen = True
    d._lesson(d.job_id, "add a layer")
    assert len(d.hands.calls) == 1
    assert len(d.tutor.asked) == 1  # it did not go on to a second step
    assert d.speaker.said[-1] == daemon.NO_CHANGE
    outcome = next(r for r in trace_lines() if r["ev"] == "outcome")
    assert (outcome["status"], outcome["code"], outcome["changed_px"]) == ("uncertain", "NO_VISIBLE_CHANGE", 0)


def test_the_model_hears_about_the_unconfirmed_action_with_the_users_next_words(make):
    d = make([click(repeat_safe=False), TALK, TALK], mode="auto")
    d.screen.frozen = True
    d._lesson(d.job_id, "add a layer")
    d._lesson(d.job_id, "Okay, continue.")
    event = d.tutor.asked[1][0]
    assert event.startswith("Okay, continue.\n(From the app, about your last action: Input sent: click at (200, 100).")
    assert "shows no visible change afterwards" in event
    d._lesson(d.job_id, "Okay, continue.")
    assert d.tutor.asked[2][0] == "Okay, continue."  # said once, not again


def test_no_visible_change_on_a_repeat_safe_action_is_reported_and_the_lesson_goes_on(make):
    d = make([click(repeat_safe=True), DONE], mode="auto")
    d.screen.frozen = True
    d._lesson(d.job_id, "open the menu")
    assert len(d.tutor.asked) == 2
    assert "shows no visible change afterwards, so the action probably did not register" in d.tutor.asked[1][0]


def test_the_comparison_starts_from_the_moment_before_the_input_not_from_the_earlier_look(make):
    d = make([click(repeat_safe=False), TALK], mode="auto")
    d.screen.script = [1, 2, 2]  # look, just before the click (the app moved on by itself), after the click
    d._lesson(d.job_id, "add a layer")
    assert [r["status"] for r in trace_lines() if r["ev"] == "outcome"] == ["uncertain"]


def test_the_screenshot_taken_after_an_action_is_the_next_look(make):
    d = make([click(), DONE], mode="auto")
    d._lesson(d.job_id, "draw a line")
    assert d.screen.captures == 3  # look, just before the click, after the click (reused for step 2)


def test_do_it_for_me_also_stops_on_no_visible_change(make):
    d = make([click(repeat_safe=False), TALK])
    d._lesson(d.job_id, "how do I add a layer?")
    d.screen.frozen = True
    d._do(d.job_id)
    assert len(d.hands.calls) == 1
    assert len(d.tutor.asked) == 1
    assert d.speaker.said[-1] == daemon.NO_CHANGE


# ---- risk ------------------------------------------------------------------------
@pytest.mark.parametrize("risk", ["sensitive", "irreversible"])
def test_risky_steps_are_never_performed_in_auto_mode_even_if_the_model_forgot_wait_user(make, risk):
    d = make([click(risk=risk, wait_user=False), click(300)], mode="auto")
    d._lesson(d.job_id, "save it")
    assert d.hands.calls == []
    assert d.speaker.said == ["Click at 200.", daemon.HOLD[risk]]
    assert len(d.tutor.asked) == 1
    assert (trace_lines()[-1]["outcome"], trace_lines()[-1]["code"]) == ("held", risk.upper())


def test_do_it_for_me_on_a_visible_sensitive_step_is_the_approval(make):
    d = make([click(risk="sensitive", wait_user=True), TALK], mode="auto")
    d._lesson(d.job_id, "save it")
    assert d.hands.calls == []
    d._do(d.job_id)
    assert d.hands.calls == [("click", 400.0, 200.0, "left", 1, "")]


def test_do_it_for_me_never_performs_an_irreversible_step(make):
    d = make([click(risk="irreversible", wait_user=True), TALK])
    d._lesson(d.job_id, "delete the file")
    d._do(d.job_id)
    assert d.hands.calls == []
    assert d.speaker.said[-1] == daemon.HOLD["irreversible"]
    assert len(d.tutor.asked) == 1


def test_a_sensitive_step_the_user_has_not_seen_yet_is_shown_not_performed(make):
    # Nothing recent on screen, so "do it for me" looks again. What comes back is new to the user.
    d = make([click(risk="sensitive"), TALK])
    d._do(d.job_id)
    assert d.hands.calls == []
    assert d.speaker.said[-1] == daemon.HOLD["sensitive"]
    d._do(d.job_id)  # now it is on screen: pressing again approves it
    assert len(d.hands.calls) == 1


# ---- trace -------------------------------------------------------------------
def test_each_look_action_and_outcome_is_traced_under_one_id(make):
    d = make([click(200, 100, risk="read_only", repeat_safe=True, expect="The tool is selected."), DONE], mode="auto")
    d._lesson(d.job_id, "draw a line")
    lines = trace_lines()
    assert [(r["ev"], r["id"]) for r in lines] == [
        ("step", f"{d.lesson}-1"), ("action", f"{d.lesson}-1"), ("outcome", f"{d.lesson}-1"), ("step", f"{d.lesson}-2"),
    ]
    step, action, outcome, _ = lines
    assert step["action"] == "click"
    assert step["point"] == [200, 100]
    assert step["logical"] == [400.0, 200.0]
    assert step["under_point"] == "pinta"
    assert step["window"] == "pinta"
    assert (step["shot"]["w"], step["shot"]["h"], step["shot"]["factor"]) == (W, H, FACTOR)
    assert step["monitor"]["name"] == "HDMI-A-1"
    assert (step["risk"], step["repeat_safe"], step["expect"]) == ("read_only", True, "The tool is selected.")
    assert (action["outcome"], action["where"]) == ("sent", "pinta")
    assert outcome["status"] == "verified"
    assert outcome["changed_px"] > 0


def test_refusal_is_traced_with_its_reason_and_code(make):
    d = make([click()], mode="auto", hands=FakeHands(refuse="That is outside pinta, where this lesson works.", code="OUTSIDE_APP"))
    d._lesson(d.job_id, "do it")
    action = trace_lines()[-1]
    assert (action["ev"], action["outcome"], action["code"]) == ("action", "refused", "OUTSIDE_APP")
    assert action["reason"] == "That is outside pinta, where this lesson works."


def test_typed_text_is_not_written_to_the_trace(make):
    d = make([Step("", "", None, False, "type", text="hunter2"), DONE], mode="auto")
    d._lesson(d.job_id, "type it")
    assert "hunter2" not in config.TRACE_FILE.read_text()
    assert trace_lines()[0]["text_len"] == 7


def test_trace_rotates_once_at_the_size_limit(make, monkeypatch):
    monkeypatch.setattr(config, "TRACE_MAX_BYTES", 200)
    d = make([TALK, TALK, TALK])
    for _ in range(3):
        d._lesson(d.job_id, "what is this?")
    old = config.TRACE_FILE.with_name("trace.jsonl.1")
    assert old.exists()
    assert len(trace_lines()) < 3


# ---- providers -------------------------------------------------------------------
def test_a_provider_never_used_before_needs_a_second_choice_and_says_where_screenshots_go(make):
    d = make([TALK])
    reply = d._switch("codex")
    assert "Screenshots of your screen would go to OpenAI, paid by your ChatGPT plan" in reply
    assert "Choose it again to confirm" in reply
    assert d.tutor.provider.id == "claude-code"
    assert d.panel.calls[-2:] == [("set_backend", ("claude-code",)), ("set_heard", (reply,))]  # the menu snaps back
    reply = d._switch("codex")
    assert reply == "Now using Codex, paid by your ChatGPT plan. The lesson carries on from where it was."
    assert d.tutor.provider.id == "codex"
    assert ("set_backend", ("codex",)) in d.panel.calls


def test_agreement_is_remembered_but_only_for_that_provider(make):
    d = make([TALK])
    d._switch("codex")
    d._switch("codex")
    d._switch("claude-code")  # the one the app started with never needs asking
    assert d.tutor.provider.id == "claude-code"
    d._switch("codex")
    assert d.tutor.provider.id == "codex"  # no second confirmation
    assert consent.allowed() == {"claude-code", "codex"}
    assert "has not been used here before" in d._switch("openai")
    assert d.tutor.provider.id == "codex"


def test_picking_something_else_in_between_does_not_count_as_confirming(make):
    d = make([TALK])
    d._switch("codex")
    d._switch("openai")
    assert "has not been used here before" in d._switch("codex")
    assert d.tutor.provider.id == "claude-code"


def test_a_paid_api_says_it_is_billed_separately_before_it_is_used(make):
    d = make([TALK])
    assert "paid by your OpenAI API key, billed per use" in d._switch("openai")
    assert d.tutor.provider.id == "claude-code"


def test_a_local_model_needs_no_agreement_because_nothing_leaves_the_computer(make):
    d = make([TALK])
    d._switch("local")
    assert d.tutor.provider.id == "local"
    assert "local" not in consent.allowed()


def test_an_unusable_or_unknown_provider_is_not_switched_to(make):
    d = make([TALK])
    assert d._switch("api") == "I can't switch to the Claude API: no ANTHROPIC_API_KEY is set."
    assert "There is no provider called gpt" in d._switch("gpt")
    assert d.tutor.provider.id == "claude-code"


def test_switching_mid_lesson_keeps_the_lesson_and_repeats_no_action(make):
    # The answer to the second look (click 999) is lost: the user interrupts while it is being thought up.
    d = make([click(200, 100), click(999, 100), click(300, 100), DONE], mode="auto")
    d.tutor.on_step = lambda: len(d.tutor.asked) == 2 and d._cancel()
    with pytest.raises(daemon.Cancelled):
        d._lesson(d.job_id, "draw a line")
    assert d.hands.calls == [("click", 400.0, 200.0, "left", 1, "")]
    before = (d.lesson, d.step_n, d.mode)
    d.tutor.on_step = None
    d._switch("codex yes")
    assert (d.lesson, d.step_n, d.mode) == before  # same lesson, same place
    assert d.hands.calls == [("click", 400.0, 200.0, "left", 1, "")]  # switching performed nothing
    d._lesson(d.job_id, "Okay, continue.")
    assert d.tutor.asked_of == ["claude-code", "claude-code", "codex", "codex"]
    assert d.hands.calls == [("click", 400.0, 200.0, "left", 1, ""), ("click", 600.0, 200.0, "left", 1, "")]  # step 1 once, step 2 once
    assert [r["backend"] for r in trace_lines() if r["ev"] == "step"] == ["claude-code", "codex", "codex"]


def test_switching_stops_a_call_in_flight_on_the_old_provider(make):
    d = make([TALK])
    old = d.tutor.provider
    d._switch("codex yes")
    assert old.aborted


def test_a_provider_failure_is_spoken_with_the_choices_and_nothing_switches(make):
    d = make([ProviderError("Claude Code could not answer.", "rate_limited")], mode="auto")
    d._job(d._lesson, "draw a line")
    for _ in range(300):  # the job runs on its own thread
        if d.speaker.said:
            break
        time.sleep(0.01)
    said = d.speaker.said[-1]
    assert said.startswith("Claude has reached its usage limit for now. You can try again, or pick ")
    assert "Codex, paid by your ChatGPT plan" in said
    assert "the OpenAI API, paid by your OpenAI API key" in said  # offered, with who pays
    assert "the Claude API" not in said  # not usable, so not offered
    assert said.endswith("I will not switch by myself.")
    assert d.tutor.provider.id == "claude-code"
    assert d.hands.calls == []
    assert trace_lines()[-1] == trace_lines()[-1] | {"ev": "provider_error", "backend": "claude-code", "code": "rate_limited"}


def test_a_failure_with_no_other_provider_usable_just_asks_to_try_again(make, monkeypatch):
    d = make([TALK])
    monkeypatch.setattr(providers, "PROVIDERS", {"claude-code": FAKES["claude-code"], "api": FAKES["api"]})
    assert d._offer(ProviderError("Claude took too long to answer.", "timeout")) == "Claude took too long to answer. Please try again in a moment."


def test_status_and_listing_name_the_provider(make):
    d = make([TALK])
    assert json.loads(d.dispatch("status", ""))["backend"] == "claude-code"
    listing = json.loads(d._switch(""))
    assert listing["backend"] == "claude-code"
    assert listing["available"] == {"claude-code": "ok", "codex": "ok", "local": "ok", "api": "no ANTHROPIC_API_KEY is set", "openai": "ok"}
