"""Guess a starting caption style from a reference reel.

This is deliberately rough. From a handful of frames we estimate:

- where the captions sit (top, middle, or bottom),
- a text color and, if a second color is obvious, a highlight color,
- how fast the reference cuts, which we turn into a pause threshold.

We do not detect the font, the point size, the animation, or whether the
letters are uppercase. The notes say that out loud. The result is a
starting point the user can edit, not a clone of the reference.
"""

from __future__ import annotations

import numpy as np

HONESTY = (
    "This is a rough guess from a few frames, not a copy of the reference. "
    "Font, animation, exact size, and uppercase are not detected."
)


def _luma(frame: np.ndarray) -> np.ndarray:
    image = frame.astype(np.float32)
    return 0.2126 * image[:, :, 0] + 0.7152 * image[:, :, 1] + 0.0722 * image[:, :, 2]


def caption_score(band: np.ndarray) -> float:
    """How much this horizontal band looks like a caption.

    Captions are a minority of bright (or brightly colored) pixels, plus
    some edges. A band that is mostly bright is a wall or a sky, not text,
    so it scores zero. A face has edges but few caption-bright pixels, so
    it scores low.
    """

    if band.size == 0:
        return 0.0
    luma = _luma(band)
    bright = luma >= 185
    bright_frac = float(bright.mean())
    if bright_frac > 0.40:
        textiness = 0.0
    else:
        textiness = bright_frac
    channel_max = band.max(axis=2).astype(np.float32)
    channel_min = band.min(axis=2).astype(np.float32)
    saturation = (channel_max - channel_min) / np.maximum(channel_max, 1.0)
    color_frac = float(((saturation > 0.40) & (luma > 110)).mean())
    if color_frac > 0.40:
        color_frac = 0.0
    gradient_y = np.abs(np.diff(luma, axis=0)).mean() if luma.shape[0] > 1 else 0.0
    gradient_x = np.abs(np.diff(luma, axis=1)).mean() if luma.shape[1] > 1 else 0.0
    edges = min(float(gradient_x + gradient_y) / 40.0, 1.0)
    return textiness * 8.0 + color_frac * 6.0 + edges * 0.35


# Bands stay clear of each other so a chin doesn't get counted as a caption.
BANDS = (
    ("top", 0.00, 0.28),
    ("middle", 0.36, 0.64),
    ("bottom", 0.72, 1.00),
)


def score_frame(frame: np.ndarray) -> dict[str, float]:
    height = frame.shape[0]
    scores = {}
    for name, start, end in BANDS:
        top = int(height * start)
        bottom = max(top + 1, int(height * end))
        scores[name] = caption_score(frame[top:bottom])
    return scores


def guess_position(frames: list[np.ndarray]) -> tuple[str, str, dict[str, float]]:
    totals = {"top": 0.0, "middle": 0.0, "bottom": 0.0}
    if not frames:
        return "bottom", "No frames to look at, so captions stay at the bottom.", totals
    for frame in frames:
        for name, score in score_frame(frame).items():
            totals[name] += score
    count = float(len(frames))
    averages = {name: value / count for name, value in totals.items()}
    ordered = sorted(averages.items(), key=lambda item: item[1], reverse=True)
    winner, best = ordered[0]
    runner_up = ordered[1][1]
    if best < 0.08 or best < runner_up * 1.35:
        return (
            "bottom",
            "No caption position stood out, so this stays at the bottom.",
            averages,
        )
    pretty = {"top": "the top", "middle": "the middle", "bottom": "the lower third"}[winner]
    return winner, f"Captions look like they sit in {pretty}.", averages


def _hex(red: int, green: int, blue: int) -> str:
    return f"#{red:02X}{green:02X}{blue:02X}"


def _quantize(pixels: np.ndarray, step: int = 24) -> list[tuple[int, int, int]]:
    if pixels.size == 0:
        return []
    snapped = (pixels.astype(np.int16) // step) * step + step // 2
    snapped = np.clip(snapped, 0, 255).astype(np.uint8)
    colors, counts = np.unique(snapped.reshape(-1, 3), axis=0, return_counts=True)
    order = np.argsort(counts)[::-1]
    return [tuple(int(channel) for channel in colors[index]) for index in order[:8]]


def colors_in_band(frames: list[np.ndarray], position: str) -> tuple[str | None, str | None, str]:
    """Most common light color, and a second saturated color if it differs."""

    band_of = {name: (start, end) for name, start, end in BANDS}
    start, end = band_of.get(position, (0.72, 1.0))
    light_pixels = []
    saturated_pixels = []
    for frame in frames:
        height = frame.shape[0]
        band = frame[int(height * start) : max(int(height * start) + 1, int(height * end))]
        luma = _luma(band)
        light = band[luma >= 185]
        if light.size:
            light_pixels.append(light.reshape(-1, 3))
        channel_max = band.max(axis=2)
        channel_min = band.min(axis=2)
        saturation = (channel_max - channel_min) / np.maximum(channel_max, 1)
        colorful = band[(saturation > 0.45) & (luma > 120)]
        if colorful.size:
            saturated_pixels.append(colorful.reshape(-1, 3))

    text_color = None
    if light_pixels:
        stacked = np.concatenate(light_pixels, axis=0)
        # Cap the sample so a long clip does not build a huge array.
        if len(stacked) > 8000:
            stacked = stacked[:: max(1, len(stacked) // 8000)]
        common = _quantize(stacked)
        if common:
            text_color = _hex(*common[0])

    accent = None
    if saturated_pixels:
        stacked = np.concatenate(saturated_pixels, axis=0)
        if len(stacked) > 8000:
            stacked = stacked[:: max(1, len(stacked) // 8000)]
        for red, green, blue in _quantize(stacked):
            candidate = _hex(red, green, blue)
            if text_color is None:
                accent = candidate
                break
            # A darker shade of the same color is the edge of the letters,
            # not a second caption color.
            if _distance(candidate, text_color) > 80 and not _same_hue(candidate, text_color):
                accent = candidate
                break

    if text_color and accent:
        note = f"A light text color near {text_color} and a second color near {accent} showed up."
    elif text_color:
        note = (
            f"A light text color near {text_color} showed up. "
            "No separate highlight color stood out."
        )
    else:
        note = "No obvious caption color stood out, so the current colors stay."
    return text_color, accent, note


def _distance(first: str, second: str) -> float:
    a = _channels(first)
    b = _channels(second)
    return sum((a[index] - b[index]) ** 2 for index in range(3)) ** 0.5


def _channels(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _same_hue(first: str, second: str) -> bool:
    """True when two colors point the same way, just at different brightness."""

    def direction(value: str):
        red, green, blue = _channels(value)
        length = (red * red + green * green + blue * blue) ** 0.5 or 1.0
        return red / length, green / length, blue / length

    one = direction(first)
    other = direction(second)
    return sum(one[index] * other[index] for index in range(3)) > 0.96


def average_shot_length(frames: list[np.ndarray], sample_fps: float, duration: float):
    """Mean time between hard cuts, estimated from frame-to-frame change.

    Frames are a couple per second, so a cut faster than that can be missed.
    """

    if duration <= 0 or len(frames) < 2 or sample_fps <= 0:
        return None, "Not enough frames to estimate pacing."
    diffs = []
    for before, after in zip(frames, frames[1:]):
        delta = np.abs(after.astype(np.int16) - before.astype(np.int16))
        diffs.append(float(delta.mean()))
    values = np.array(diffs, dtype=np.float64)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median))) or 1.0
    threshold = max(18.0, median + 4.0 * mad)
    cuts = 0
    for index, value in enumerate(values):
        if value < threshold:
            continue
        previous = values[index - 1] if index else 0.0
        nxt = values[index + 1] if index + 1 < len(values) else 0.0
        if value >= previous and value >= nxt:
            cuts += 1
    average = duration / (cuts + 1)
    note = (
        f"About {cuts} hard cuts over {duration:.0f}s, so an average shot near {average:.1f}s. "
        f"Frames were sampled at {sample_fps:.0f} per second, so faster cuts can be missed."
    )
    return average, note


def pacing_suggestion(average_shot: float | None) -> dict:
    """Turn an average shot length into a pause threshold and a line length."""

    if average_shot is None:
        return {
            "pause_threshold": None,
            "padding": None,
            "words_per_line": None,
            "note": "Pacing was not estimated, so the pause settings stay as they are.",
        }
    if average_shot < 2.2:
        return {
            "pause_threshold": 0.28,
            "padding": 0.05,
            "words_per_line": 3,
            "note": "The reference cuts quickly, so the pause threshold starts tight and lines stay short.",
        }
    if average_shot < 4.5:
        return {
            "pause_threshold": 0.40,
            "padding": 0.08,
            "words_per_line": 4,
            "note": "The reference has a medium pace, so the pause threshold starts in the middle.",
        }
    return {
        "pause_threshold": 0.60,
        "padding": 0.12,
        "words_per_line": 6,
        "note": "The reference holds shots longer, so short pauses are kept and lines can be longer.",
    }


def analyze_frames(frames: list[np.ndarray], sample_fps: float, duration: float) -> dict:
    """Pure analysis. `frames` are RGB uint8 images in time order."""

    position, position_note, scores = guess_position(frames)
    text_color, accent, color_note = colors_in_band(frames, position)
    average, pace_note = average_shot_length(frames, sample_fps, duration)
    pacing = pacing_suggestion(average)
    notes = [position_note, color_note, pacing["note"], pace_note, HONESTY]
    suggestion = {
        "position": position,
        "text_color": text_color,
        "highlight_color": accent,
        "pause_threshold": pacing["pause_threshold"],
        "padding": pacing["padding"],
        "words_per_line": pacing["words_per_line"],
        "uppercase": None,
    }
    return {
        "notes": notes,
        "suggestion": suggestion,
        "scores": {name: round(value, 3) for name, value in scores.items()},
        "average_shot": None if average is None else round(average, 2),
    }
