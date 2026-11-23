"""Decide which parts of a clip are dead air.

The idea is simple:

1. A pause is a stretch where no kept word is being said.
2. Short pauses stay. They are breaths the speaker meant to leave in.
   `pause_threshold` is that length, in seconds.
3. Each side of a cut keeps a little extra picture (`padding`) so the
   jump does not land exactly on a consonant. Padding can also swallow
   a cut that was only barely over the threshold.
4. A long gap that is still loud (laughter, music, a door) is not dead
   air, so it stays. Quiet gaps go.

Word timings do the first three steps. The volume check is step 4, and
it only overrides a cut when the gap is clearly loud compared with the
speech around it. A noisy room does not disable the threshold.

Deleted words and "cut this segment" marks are holes. They are removed
after padding, and a loud-gap rescue is not allowed to put them back.
"""

from __future__ import annotations

import numpy as np

from pith.ranges import (
    MIN_RANGE,
    clamp_ranges,
    complement_ranges,
    merge_ranges,
    overlap_amount,
    subtract_ranges,
)

# A gap is "still has sound" when its volume is at least this fraction of
# the quieter part of the speech. 0.55 means the gap has to be more than
# half as loud as a soft spoken word before we refuse to cut it.
LOUD_GAP_RATIO = 0.55


def speech_keeps(words, duration: float, threshold: float, padding: float):
    """Kept stretches implied by the words alone, before holes and volume."""

    active = [word for word in words if not word.deleted and word.end > word.start]
    active.sort(key=lambda word: (word.start, word.end))
    if not active:
        return []

    phrases = []
    start, end = active[0].start, active[0].end
    for word in active[1:]:
        # A gap shorter than the threshold is inside the phrase, so the
        # pause between these two words is kept on purpose.
        if word.start - end < threshold:
            end = max(end, word.end)
        else:
            phrases.append((start, end))
            start, end = word.start, word.end
    phrases.append((start, end))

    padded = []
    for phrase_start, phrase_end in phrases:
        padded.append(
            (
                max(0.0, phrase_start - padding),
                min(duration, phrase_end + padding),
            )
        )
    return merge_ranges(padded)


def deleted_spans(words):
    spans = []
    for word in words:
        if word.deleted and word.end > word.start:
            spans.append((float(word.start), float(word.end)))
    return merge_ranges(spans)


def _speech_mask(rms: np.ndarray, hop: float, words) -> np.ndarray:
    mask = np.zeros(len(rms), dtype=bool)
    if hop <= 0:
        return mask
    for word in words:
        if word.deleted:
            continue
        first = max(0, int(word.start / hop))
        last = min(len(rms), int(word.end / hop) + 1)
        if last > first:
            mask[first:last] = True
    return mask


def loud_line(rms: np.ndarray | None, hop: float, words) -> float | None:
    """Volume above which a gap is too loud to call dead air.

    Returns None when we cannot tell speech from silence, in which case
    the word timings win and nothing is rescued.
    """

    if rms is None or len(rms) == 0 or hop <= 0:
        return None
    mask = _speech_mask(rms, hop, words)
    if int(mask.sum()) < 5:
        return None
    values = np.sort(rms[mask].astype(np.float64))
    quartile = float(values[len(values) // 4])
    if quartile <= 1e-6:
        return None
    return quartile * LOUD_GAP_RATIO


def median_rms(rms: np.ndarray, hop: float, start: float, end: float) -> float:
    if hop <= 0 or len(rms) == 0 or end <= start:
        return 0.0
    first = max(0, int(start / hop))
    last = min(len(rms), int(np.ceil(end / hop)))
    if last <= first:
        return 0.0
    return float(np.median(rms[first:last]))


def _mostly_inside(start: float, end: float, holes) -> bool:
    length = end - start
    if length <= 0 or not holes:
        return False
    return overlap_amount(start, end, holes) >= 0.5 * length


def apply_energy(keeps, duration: float, rms, hop: float, words, holes):
    """Put clearly-loud gaps back into the keep list.

    Returns (keeps, loud_gaps). Loud gaps are the cuts we refused.
    """

    level = loud_line(rms, hop, words)
    if level is None or rms is None:
        return keeps, []
    protected = []
    for start, end in complement_ranges(keeps, duration, min_len=0.08):
        # A word the user deleted is loud, because it is speech. That
        # must not count as a reason to put the word back.
        if _mostly_inside(start, end, holes):
            continue
        if median_rms(rms, hop, start, end) >= level:
            protected.append((start, end))
    if not protected:
        return keeps, []
    return merge_ranges(list(keeps) + protected), protected


def compute_keeps(words, duration, threshold, padding, restored, forced_cuts, rms=None, hop=0.02):
    """The final keep list for a clip.

    `restored` and `forced_cuts` are lists of (start, end) from the editor.
    Forced cuts win over restores: if you cut a segment, it stays cut
    until you undo that cut.
    """

    if duration <= 0:
        return [], []

    holes = merge_ranges(list(deleted_spans(words)) + list(forced_cuts))
    active = [word for word in words if not word.deleted]
    if not words:
        # No transcript at all. Keep the picture, minus anything the user
        # explicitly cut. "No speech" and "not transcribed yet" both land here.
        base = [(0.0, float(duration))]
        loud = []
    elif not active:
        # Every word was removed. Do not let the volume check put them back.
        base = []
        loud = []
    else:
        base = speech_keeps(words, duration, threshold, padding)
        base = subtract_ranges(base, holes)
        base, loud = apply_energy(base, duration, rms, hop, words, holes)

    base = merge_ranges(list(base) + list(restored))
    base = subtract_ranges(base, holes)
    base = [
        (start, end)
        for start, end in clamp_ranges(base, duration)
        if end - start >= MIN_RANGE
    ]
    return base, loud
