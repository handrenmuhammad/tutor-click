"""Lesson records on disk: what is kept, what is offered again, and the bounds."""
import json
import os

from tutor_click import config, lessons
from tutor_click.brain import Step
from tutor_click.lessons import Lesson

CLICK = Step("Click the brush.", "Pick the brush", (100, 100), False, "click", expect="The brush is highlighted.", concept="Paintbrush tool")
DONE = Step("All done.", "Done", None, True)


def saved(goal="draw a circle", *steps: Step) -> Lesson:
    lesson = Lesson(goal=goal)
    for step in steps or (CLICK,):
        lesson.add_step(step, "guide")
    lesson.save()
    return lesson


def age(lesson: Lesson, secs: float):
    """Make a record look older: 'most recent' goes by the file's time."""
    path = config.LESSONS_DIR / f"{lesson.id}.json"
    os.utime(path, (path.stat().st_mtime - secs,) * 2)


def test_a_saved_lesson_comes_back_as_it_was():
    lesson = saved()
    lesson.outcome("reported", "NO_VISIBLE_CHANGE")
    lesson.memory = {"asks": ["draw a circle"], "history": [["draw a circle", "Click the brush."]], "concepts": ["Paintbrush tool"]}
    lesson.save()
    back = lessons.resumable()
    assert back == lesson
    assert back.steps[0] == back.steps[0] | {"n": 1, "action": "click at (100, 100)", "outcome": "reported", "code": "NO_VISIBLE_CHANGE"}


def test_nothing_to_resume_without_records_or_after_closing_or_finishing():
    assert lessons.resumable() is None
    saved().close()
    assert lessons.resumable() is None
    saved("finish it", CLICK, DONE)
    assert lessons.resumable() is None


def test_a_lesson_with_no_steps_writes_nothing_even_when_closed():
    Lesson().close()
    assert not config.LESSONS_DIR.exists()


def test_only_the_most_recent_lesson_is_considered():
    old = saved("the old one")
    age(old, 100)
    saved("the new one", DONE)  # finished, so there is nothing to offer: the older open one stays buried
    assert lessons.resumable() is None


def test_a_finished_lesson_that_goes_on_is_open_again():
    lesson = saved("draw a circle", DONE, CLICK)
    assert lessons.resumable().id == lesson.id


def test_steps_are_bounded_but_the_count_goes_on():
    lesson = Lesson()
    for _ in range(config.LESSON_MAX_STEPS + 7):
        lesson.add_step(CLICK, "auto")
    assert len(lesson.steps) == config.LESSON_MAX_STEPS
    assert (lesson.steps[0]["n"], lesson.steps[-1]["n"], lesson.step_n) == (8, config.LESSON_MAX_STEPS + 7, config.LESSON_MAX_STEPS + 7)


def test_records_are_bounded_and_the_oldest_go_first():
    made = []
    for i in range(config.LESSONS_KEPT + 3):
        made.append(saved(f"lesson {i}"))
        age(made[-1], 1000 - i)  # the tests write faster than file times tick
    newest = saved("one more")
    left = {p.stem for p in config.LESSONS_DIR.glob("*.json")}
    assert len(left) == config.LESSONS_KEPT
    assert left == {m.id for m in made[-(config.LESSONS_KEPT - 1):]} | {newest.id}


def test_a_kill_in_the_middle_of_a_save_leaves_the_last_complete_record():
    lesson = saved()
    (config.LESSONS_DIR / f"{lesson.id}.tmp").write_text('{"id": "' + lesson.id + '", "steps": [{"n": 1')  # died while writing
    assert lessons.resumable() == lesson
    lesson.add_step(CLICK, "guide")
    lesson.save()  # the next save replaces the leftover
    assert lessons.resumable().step_n == 2
    assert [p.name for p in config.LESSONS_DIR.iterdir()] == [f"{lesson.id}.json"]


def test_a_record_this_version_cannot_read_is_not_offered():
    lesson = saved()
    path = config.LESSONS_DIR / f"{lesson.id}.json"
    path.write_text("{not json")
    assert lessons.resumable() is None
    path.write_text(json.dumps({"id": lesson.id, "from_another_version": True}))
    assert lessons.resumable() is None


def test_a_disk_that_cannot_be_written_does_not_stop_the_lesson(monkeypatch, tmp_path, capsys):
    (tmp_path / "a-file").write_text("")
    monkeypatch.setattr(config, "LESSONS_DIR", tmp_path / "a-file" / "lessons")
    saved()
    assert "lesson record:" in capsys.readouterr().err
    assert lessons.resumable() is None
