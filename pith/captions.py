"""Burned-in captions, as an Advanced SubStation Alpha (.ass) script.

ffmpeg draws the script onto the video with libass. We use one caption
line at a time (a handful of words) and rewrite that line once per word,
with the active word in the highlight color. That is the whole "word by
word" effect. Karaoke is the same trick, except earlier words stay lit.

Times in the script are output times: the clock of the exported file,
after pauses have been removed. Passing source times would make the
captions drift ahead of the audio every time we cut something.

Font size is written for a 1920-pixel-tall frame and scaled to the real
frame, so "104" means the same visual size on a phone reel and on a
720p export.
"""

from __future__ import annotations

from pith.presets import Style


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB to libass &HAABBGGRR. Libass stores blue in the high byte."""

    text = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(text) != 6:
        text = "FFFFFF"
    try:
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
    except ValueError:
        red, green, blue = 255, 255, 255
    alpha = max(0, min(255, int(alpha)))
    return f"&H{alpha:02X}{blue:02X}{green:02X}{red:02X}"


def ass_timestamp(seconds: float) -> str:
    """H:MM:SS.cs — centiseconds, which is as fine as ASS gets."""

    if seconds < 0:
        seconds = 0.0
    centiseconds = int(round(seconds * 100))
    whole, centiseconds = divmod(centiseconds, 100)
    minutes, secs = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def escape_ass(text: str) -> str:
    """Stop caption text from being read as override tags."""

    cleaned = text.replace("\r", " ").replace("\n", " ")
    return cleaned.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def display_text(text: str, uppercase: bool) -> str:
    collapsed = " ".join(text.split())
    if uppercase:
        return collapsed.upper()
    return collapsed


def group_lines(words: list[dict], words_per_line: int, max_gap: float = 0.45) -> list[list[dict]]:
    """Group caption words into lines.

    A line breaks when it is full, or when the next word starts well
    after the previous one ended. That second rule keeps a caption from
    hanging on screen across a restored pause.
    `words` must already be in output-time order.
    """

    per_line = max(1, int(words_per_line))
    lines: list[list[dict]] = []
    current: list[dict] = []

    def source_start(word: dict) -> float:
        # Prefer the original timestamp. Two words that only became neighbors
        # because a pause was cut should not share a caption line: on the
        # source preview that line would sit on screen across the gap.
        return float(word.get("start", word["out_start"]))

    def source_end(word: dict) -> float:
        return float(word.get("end", word["out_end"]))

    for word in words:
        if not current:
            current = [word]
            continue
        gap = source_start(word) - source_end(current[-1])
        if len(current) >= per_line or gap > max_gap:
            lines.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(current)
    return lines


def _colored(token: str, color: str) -> str:
    return "{\\c" + ass_color(color) + "&}" + escape_ass(token)


def render_line(words: list[dict], active_index: int, style: Style) -> str:
    parts = []
    for index, word in enumerate(words):
        if style.highlight_mode == "cumulative":
            hot = index <= active_index
        elif style.highlight_mode == "active":
            hot = index == active_index
        else:
            hot = False
        color = style.highlight_color if hot else style.text_color
        parts.append(_colored(display_text(word["text"], style.uppercase), color))
    return " ".join(parts)


def line_events(words: list[dict], style: Style) -> list[tuple[float, float, str]]:
    """One dialogue event per spoken word, so the highlight can move."""

    events = []
    count = len(words)
    for index, word in enumerate(words):
        start = float(word["out_start"])
        if index + 1 < count:
            # Hold this highlight until the next word starts, so the line
            # does not blink off during a tiny gap inside the phrase.
            end = float(words[index + 1]["out_start"])
        else:
            end = float(word["out_end"])
        if end < start + 0.05:
            end = start + 0.05
        events.append((start, end, render_line(words, index, style)))
    return events


def build_events(lines: list[list[dict]], style: Style) -> list[tuple[float, float, str]]:
    events = []
    for line in lines:
        events.extend(line_events(line, style))
    # A rounding collision can make two events share a boundary. Nudge
    # anything that would run backwards so libass does not drop it.
    fixed = []
    for start, end, text in events:
        if fixed and start < fixed[-1][1]:
            start = fixed[-1][1]
        if end < start + 0.04:
            end = start + 0.04
        fixed.append((start, end, text))
    return fixed


def _font_pixels(style: Style, play_y: int) -> int:
    return max(18, int(round(style.font_size * play_y / 1920)))


def build_ass(style: Style, lines: list[list[dict]], play_x: int, play_y: int) -> str:
    """A complete .ass document for a frame of play_x by play_y."""

    alignment = {"bottom": 2, "middle": 5, "top": 8}.get(style.position, 2)
    if style.position == "middle":
        margin_v = int(play_y * 0.02)
    elif style.position == "top":
        margin_v = int(play_y * 0.08)
    else:
        margin_v = int(play_y * 0.11)
    font_px = _font_pixels(style, play_y)
    bold = -1 if style.bold else 0
    if style.boxed:
        # BorderStyle 3 draws a box. Outline is the padding, and the
        # outline color is the box color. A warm near-black reads as a pill.
        border_style = 3
        outline = max(10, int(font_px * 0.32))
        shadow = 0
        outline_colour = "&H00110F0C"
        back_colour = "&H00000000"
    else:
        border_style = 1
        outline = max(3, int(round(font_px * 0.07)))
        shadow = max(0, int(round(font_px * 0.04)))
        outline_colour = "&H00110F0C"
        back_colour = "&H78000000"

    primary = ass_color(style.text_color)
    secondary = ass_color(style.highlight_color)
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {int(play_x)}",
            f"PlayResY: {int(play_y)}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            (
                f"Style: Caption,{style.font},{font_px},{primary},{secondary},"
                f"{outline_colour},{back_colour},{bold},0,0,0,100,100,0,0,"
                f"{border_style},{outline},{shadow},{alignment},60,60,{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    body = []
    for start, end, text in build_events(lines, style):
        body.append(
            f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Caption,,0,0,0,,{text}"
        )
    return header + "\n" + "\n".join(body) + "\n"
