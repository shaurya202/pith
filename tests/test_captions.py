"""Caption timing. The interesting part is that times are on the output clock."""

from pith.captions import ass_timestamp, build_ass, escape_ass, group_lines
from pith.plan import build_plan
from pith.presets import preset_style
from pith.project import Project, Word


def _project(words, duration=5.0, threshold=0.4, padding=0.0):
    project = Project(
        id="abc123def456",
        name="t",
        source_name="t.mp4",
        source_ext=".mp4",
        duration=duration,
        width=320,
        height=240,
        fps=25,
        has_audio=True,
        created=0,
        updated=0,
        words=words,
        transcript_done=True,
        pause_threshold=threshold,
        padding=padding,
        style=preset_style("hormozi"),
    )
    return project


def test_uppercase_and_highlight_steps():
    words = [
        Word(1, "cut", 0.2, 0.5),
        Word(2, "the", 0.5, 0.8),
        Word(3, "pauses", 0.8, 1.2),
    ]
    plan = build_plan(_project(words, duration=1.5), None)
    style = preset_style("hormozi")
    ass = build_ass(style, [line["words"] and _spoken(plan, line) for line in plan["lines"]], 1080, 1920)
    # The line is rebuilt from full-precision spoken words below. This call
    # uses the rounded plan lines, which is what the window sees; the
    # exporter uses the unrounded ones. Either way the text is the point.
    assert "CUT" in ass
    assert "THE" in ass
    assert "PAUSES" in ass
    assert "cut the" not in ass.lower() or "CUT" in ass
    # One event per word, so the highlight can move.
    assert ass.count("Dialogue:") == 3


def _spoken(plan, line):
    return line["words"]


def test_deleted_word_is_absent_and_times_skip_the_cut():
    words = [
        Word(1, "hello", 0.2, 0.6),
        Word(2, "um", 1.5, 1.8),
        Word(3, "there", 3.0, 3.4),
    ]
    words[1].deleted = True
    plan = build_plan(_project(words, duration=4.0, padding=0.0), None)
    texts = [word["text"] for word in plan["words"] if word["included"]]
    assert texts == ["hello", "there"]
    assert all(word["text"] != "um" or not word["included"] for word in plan["words"])
    style = preset_style("minimal")
    style.uppercase = False
    spoken = plan["_spoken"]
    ass = build_ass(style, group_lines(spoken, style.words_per_line), 640, 360)
    assert "um" not in ass.lower()
    assert "hello" in ass
    # "there" starts at 3.0. The keep before it is just "hello" (0.2-0.6),
    # and the deleted word is a hole, so the output time is well under 3s.
    there = next(word for word in spoken if word["text"] == "there")
    assert there["out_start"] < 2.0
    assert ass_timestamp(there["out_start"]) in ass


def test_a_long_source_gap_breaks_the_line_even_if_the_cut_closes_it():
    words = [
        {"text": "one", "start": 0.0, "end": 0.3, "out_start": 0.0, "out_end": 0.3},
        {"text": "two", "start": 2.0, "end": 2.3, "out_start": 0.3, "out_end": 0.6},
    ]
    lines = group_lines(words, words_per_line=4, max_gap=0.45)
    assert len(lines) == 2


def test_short_gap_stays_on_one_line_until_the_line_is_full():
    words = [
        {"text": "a", "start": 0.0, "end": 0.2, "out_start": 0.0, "out_end": 0.2},
        {"text": "b", "start": 0.3, "end": 0.5, "out_start": 0.3, "out_end": 0.5},
        {"text": "c", "start": 0.6, "end": 0.8, "out_start": 0.6, "out_end": 0.8},
    ]
    assert len(group_lines(words, words_per_line=2)) == 2
    assert len(group_lines(words, words_per_line=3)) == 1


def test_braces_in_a_word_are_escaped():
    assert r"\{" in escape_ass("hello {world}")
    words = [Word(1, "hello {world}", 0.1, 0.4)]
    plan = build_plan(_project(words, duration=1.0), None)
    ass = build_ass(preset_style("minimal"), group_lines(plan["_spoken"], 3), 320, 240)
    assert r"\{world\}" in ass
    assert "Dialogue: 0,0:00:00." in ass


def test_karaoke_keeps_earlier_words_lit():
    style = preset_style("karaoke")
    spoken = [
        {"text": "one", "start": 0.0, "end": 0.3, "out_start": 0.0, "out_end": 0.3},
        {"text": "two", "start": 0.3, "end": 0.6, "out_start": 0.3, "out_end": 0.6},
    ]
    ass = build_ass(style, group_lines(spoken, 4), 720, 1280)
    # During the second word, both words use the highlight color.
    # #FFE566 is stored by libass as BGR, so the bytes are 66 E5 FF.
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == 2
    assert dialogues[1].lower().count("66e5ff") >= 2


def test_timestamp_format_and_negative_clamp():
    assert ass_timestamp(0) == "0:00:00.00"
    assert ass_timestamp(65.2) == "0:01:05.20"
    assert ass_timestamp(-3) == "0:00:00.00"
