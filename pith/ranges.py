"""Time-range arithmetic.

A range is a pair of seconds, (start, end), with end after start.
The editor keeps some ranges and cuts the rest. These helpers only do
the arithmetic: merge overlaps, punch holes, and find the gaps.

Nothing here knows about words or video, which is why the tests are short.
"""

from __future__ import annotations

# Gaps smaller than this are treated as "the same moment". Frame timing
# and floating point both leave dust around a thousandth of a second.
EPS = 1e-3

# A keep or a cut shorter than this is not worth a jump cut. 40ms is
# about one frame at 24fps, which the eye reads as a glitch, not a cut.
MIN_RANGE = 0.04


def _clean(ranges):
    cleaned = []
    for start, end in ranges:
        start = float(start)
        end = float(end)
        if end - start < EPS:
            continue
        if start > end:
            start, end = end, start
        cleaned.append((start, end))
    cleaned.sort()
    return cleaned


def merge_ranges(ranges, bridge: float = 0.0):
    """Sort ranges and fuse ones that overlap or nearly touch.

    `bridge` also joins ranges separated by a gap of that size or less.
    Silence detection uses it for "this pause is short enough to keep".
    """

    cleaned = _clean(ranges)
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + bridge + EPS:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def clamp_ranges(ranges, duration: float):
    """Keep only the part of each range that sits inside [0, duration]."""

    if duration <= 0:
        return []
    clipped = []
    for start, end in ranges:
        start = max(0.0, float(start))
        end = min(float(duration), float(end))
        if end - start >= EPS:
            clipped.append((start, end))
    return merge_ranges(clipped)


def subtract_ranges(base, holes):
    """Return the parts of `base` that are not covered by `holes`."""

    result = []
    hole_list = merge_ranges(holes)
    for start, end in merge_ranges(base):
        cursor = start
        for hole_start, hole_end in hole_list:
            if hole_end <= cursor + EPS:
                continue
            if hole_start >= end - EPS:
                break
            if hole_start > cursor + EPS:
                result.append((cursor, min(hole_start, end)))
            cursor = max(cursor, hole_end)
            if cursor >= end - EPS:
                break
        if end - cursor >= EPS:
            result.append((cursor, end))
    return merge_ranges(result)


def complement_ranges(keeps, duration: float, min_len: float = MIN_RANGE):
    """The gaps between keeps, inside [0, duration]."""

    if duration <= 0:
        return []
    cuts = []
    cursor = 0.0
    for start, end in merge_ranges(clamp_ranges(keeps, duration)):
        if start - cursor >= min_len:
            cuts.append((round_pair(cursor, start)))
        cursor = max(cursor, end)
    if duration - cursor >= min_len:
        cuts.append((cursor, float(duration)))
    return cuts


def round_pair(start: float, end: float):
    return (float(start), float(end))


def overlap_amount(start: float, end: float, ranges) -> float:
    total = 0.0
    for keep_start, keep_end in ranges:
        left = max(start, keep_start)
        right = min(end, keep_end)
        if right > left:
            total += right - left
    return total


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return min(a_end, b_end) - max(a_start, b_start) > EPS


def range_duration(ranges) -> float:
    return float(sum(end - start for start, end in ranges))


def map_source_to_output(time: float, keeps) -> float:
    """Where a source timestamp lands after the cuts are closed up.

    Time spent in earlier cuts disappears. If `time` itself is inside a
    cut, this returns the output time of that cut point (the end of the
    previous kept stretch). Caption code only asks about times it kept.
    """

    output = 0.0
    for start, end in keeps:
        if time < start:
            return output
        if time <= end:
            return output + (time - start)
        output += end - start
    return output


def first_overlap(start: float, end: float, keeps):
    """The first piece of [start, end] that sits inside a keep, or None."""

    for keep_start, keep_end in keeps:
        left = max(start, keep_start)
        right = min(end, keep_end)
        if right - left >= EPS:
            return (left, right)
    return None


def snap_span(target, spans):
    """If `target` is mostly the same as a known span, return that span.

    The window sends times rounded to the millisecond. Snapping back to
    the span we actually computed keeps "put this pause back" exact.
    """

    start, end = float(target[0]), float(target[1])
    length = max(0.0, end - start)
    best = None
    best_overlap = 0.0
    for span in spans:
        amount = overlap_amount(start, end, [span])
        if amount > best_overlap:
            best = (float(span[0]), float(span[1]))
            best_overlap = amount
    if best is None or length <= 0:
        return (start, end)
    if best_overlap >= 0.6 * min(length, best[1] - best[0]):
        return best
    return (start, end)
