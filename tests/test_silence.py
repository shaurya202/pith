"""Pause detection. These tests describe the edit in seconds, not in video files."""

from pith.project import Word
from pith.ranges import map_source_to_output, range_duration
from pith.silence import compute_keeps, speech_keeps

import numpy as np


def word(index, text, start, end, deleted=False):
    return Word(id=index, text=text, start=start, end=end, deleted=deleted)


def test_short_pause_is_kept_and_long_pause_is_cut():
    words = [
        word(1, "hi", 0.5, 0.8),
        word(2, "there", 0.9, 1.2),  # 0.1s gap, under the 0.45s threshold
        word(3, "friend", 3.0, 3.4),  # 1.8s gap, cut
    ]
    keeps, _loud = compute_keeps(words, duration=5.0, threshold=0.45, padding=0.1, restored=[], forced_cuts=[])
    # Padding pulls 0.1s onto each side of the two phrases.
    assert keeps == [(0.4, 1.3), (2.9, 3.5)]
    assert abs(range_duration(keeps) - 1.5) < 1e-9


def test_padding_can_swallow_a_cut_that_barely_qualified():
    words = [word(1, "a", 0.0, 0.2), word(2, "b", 0.45, 0.7)]
    # The gap is 0.25s, just over a 0.2s threshold, but 0.15s of padding
    # on each side overlaps and the cut disappears.
    keeps, _ = compute_keeps(words, duration=1.0, threshold=0.2, padding=0.15, restored=[], forced_cuts=[])
    assert keeps == [(0.0, 0.85)]


def test_leading_and_trailing_silence_are_cuts():
    words = [word(1, "hello", 1.0, 1.4)]
    keeps, _ = compute_keeps(words, duration=3.0, threshold=0.4, padding=0.0, restored=[], forced_cuts=[])
    assert keeps == [(1.0, 1.4)]


def test_deleted_word_becomes_a_hole_even_inside_a_phrase():
    words = [
        word(1, "a", 1.0, 1.2),
        word(2, "um", 1.3, 1.5, deleted=True),
        word(3, "c", 1.6, 1.8),
    ]
    # The gap from a to c is 0.4s, under the threshold, so they would have
    # been one phrase. Removing "um" punches that time back out.
    keeps, _ = compute_keeps(words, duration=3.0, threshold=0.45, padding=0.0, restored=[], forced_cuts=[])
    assert keeps == [(1.0, 1.3), (1.5, 1.8)]


def test_restore_puts_a_pause_back_and_force_cut_removes_speech():
    words = [word(1, "hi", 0.2, 0.5), word(2, "there", 2.0, 2.4)]
    keeps, _ = compute_keeps(
        words,
        duration=3.0,
        threshold=0.4,
        padding=0.0,
        restored=[(0.5, 2.0)],
        forced_cuts=[],
    )
    assert keeps == [(0.2, 2.4)]

    keeps, _ = compute_keeps(
        words,
        duration=3.0,
        threshold=0.4,
        padding=0.0,
        restored=[],
        forced_cuts=[(0.2, 0.5)],
    )
    assert keeps == [(2.0, 2.4)]


def test_deleting_every_word_keeps_nothing():
    words = [word(1, "hi", 0.2, 0.6, deleted=True)]
    keeps, _ = compute_keeps(words, duration=2.0, threshold=0.4, padding=0.1, restored=[], forced_cuts=[])
    assert keeps == []


def test_no_words_keeps_the_whole_clip():
    keeps, _ = compute_keeps([], duration=2.0, threshold=0.4, padding=0.1, restored=[], forced_cuts=[])
    assert keeps == [(0.0, 2.0)]


def test_output_time_skips_the_cut():
    keeps = [(0.4, 1.3), (2.9, 3.5)]
    # "there" started at 0.9 in the source, 0.5s into the first keep.
    assert abs(map_source_to_output(0.9, keeps) - 0.5) < 1e-9
    # "friend" started at 3.0, which is 0.1s into the second keep,
    # after the whole first keep (0.9s).
    assert abs(map_source_to_output(3.0, keeps) - 1.0) < 1e-9


def test_loud_gap_is_not_cut_and_a_quiet_gap_is():
    words = [word(1, "hi", 0.0, 0.5), word(2, "there", 2.0, 2.5)]
    hop = 0.02
    frames = int(3.0 / hop)
    rms = np.zeros(frames, dtype=np.float32)
    # Speech is loud. The gap in the middle is also loud: music, not silence.
    rms[:] = 0.05
    rms[int(0.0 / hop) : int(0.5 / hop)] = 1.0
    rms[int(2.0 / hop) : int(2.5 / hop)] = 1.0
    rms[int(0.5 / hop) : int(2.0 / hop)] = 0.8
    keeps, loud = compute_keeps(
        words, duration=3.0, threshold=0.4, padding=0.0, restored=[], forced_cuts=[], rms=rms, hop=hop
    )
    assert loud, "expected the loud gap to be reported"
    assert keeps[0][0] == 0.0 and keeps[-1][1] == 2.5

    quiet = np.zeros(frames, dtype=np.float32)
    quiet[int(0.0 / hop) : int(0.5 / hop)] = 1.0
    quiet[int(2.0 / hop) : int(2.5 / hop)] = 1.0
    keeps, loud = compute_keeps(
        words, duration=3.0, threshold=0.4, padding=0.0, restored=[], forced_cuts=[], rms=quiet, hop=hop
    )
    assert loud == []
    assert keeps == [(0.0, 0.5), (2.0, 2.5)]


def test_a_medium_gap_still_follows_the_threshold():
    # Loud enough to be room tone, not loud enough to veto the cut.
    words = [word(1, "hi", 0.0, 0.5), word(2, "there", 2.0, 2.5)]
    hop = 0.02
    frames = int(3.0 / hop)
    rms = np.zeros(frames, dtype=np.float32)
    rms[int(0.0 / hop) : int(0.5 / hop)] = 1.0
    rms[int(2.0 / hop) : int(2.5 / hop)] = 1.0
    rms[int(0.5 / hop) : int(2.0 / hop)] = 0.3
    keeps, loud = compute_keeps(
        words, duration=3.0, threshold=0.4, padding=0.0, restored=[], forced_cuts=[], rms=rms, hop=hop
    )
    assert loud == []
    assert keeps == [(0.0, 0.5), (2.0, 2.5)]


def test_speech_keeps_merge_order_is_stable():
    words = [word(1, "b", 1.0, 1.2), word(2, "a", 0.0, 0.2)]
    assert speech_keeps(words, 2.0, threshold=0.3, padding=0.0) == [(0.0, 0.2), (1.0, 1.2)]
