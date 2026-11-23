"""Reference matching on drawn frames, so the test does not need a video file."""

import numpy as np

from pith.reference import analyze_frames, caption_score, guess_position


def _frame(width=160, height=284, color=(10, 10, 12)):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


def test_bottom_white_bar_is_read_as_a_lower_third_caption():
    frame = _frame()
    # A short white bar in the bottom band, like a caption, not a white wall.
    frame[250:270, 30:130] = (245, 245, 245)
    position, note, _scores = guess_position([frame, frame])
    assert position == "bottom"
    assert "lower third" in note
    assert caption_score(frame[200:284]) > caption_score(frame[100:180])


def test_a_bright_wall_is_not_called_a_caption():
    frame = _frame(color=(240, 240, 240))
    assert caption_score(frame) == 0


def test_top_yellow_type_suggests_position_and_a_tight_pace():
    frames = []
    # Dark, but different enough that the shot detector sees a hard cut.
    for color in ((6, 8, 28), (70, 12, 16), (8, 60, 24), (64, 40, 8), (10, 10, 10), (48, 16, 40)):
        frame = _frame(color=color)
        frame[20:48, 40:120] = (255, 225, 74)
        frames.append(frame)
    # Big jumps between frames so they read as hard cuts.
    analysis = analyze_frames(frames, sample_fps=2.0, duration=3.0)
    assert analysis["suggestion"]["position"] == "top"
    assert analysis["suggestion"]["text_color"] is not None
    text = analysis["suggestion"]["text_color"].lstrip("#")
    red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    assert red > 180 and green > 160 and blue < 140
    assert analysis["suggestion"]["pause_threshold"] == 0.28
    assert any("not a copy" in note for note in analysis["notes"])
    assert any("uppercase" in note.lower() for note in analysis["notes"])
