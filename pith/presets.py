"""Caption looks.

A preset is a starting Style. After you pick one, every field can still
change. To add a look, add a Style here. The window builds its buttons
from this list, so there is no second place to register it.

Font files live in pith/fonts and are licensed under the SIL Open Font
License (see pith/fonts/licenses). The family names below have to match
the names baked into those files, or ffmpeg will fall back to a default.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


# Inter is shipped as a real bold face. Anton and Oswald already are heavy,
# and asking libass to faux-bold them makes the strokes muddy.
FONT_BOLD = {
    "Anton": False,
    "Inter": True,
    "Oswald": False,
}

CAPTION_FONTS = list(FONT_BOLD)


@dataclass
class Style:
    preset: str = "hormozi"
    font: str = "Anton"
    font_size: int = 104
    text_color: str = "#FFFFFF"
    highlight_color: str = "#E6FF3A"
    position: str = "bottom"  # top, middle, bottom
    words_per_line: int = 3
    uppercase: bool = True
    # active: only the word being spoken is highlighted.
    # cumulative: words stay lit once spoken (karaoke).
    # none: the whole line is one color.
    highlight_mode: str = "active"
    boxed: bool = False
    bold: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Style":
        data = dict(data or {})
        known = {item.name for item in fields(cls)}
        values = {key: data[key] for key in known if key in data}
        style = cls(**values)
        if style.font in FONT_BOLD and "bold" not in data:
            style.bold = FONT_BOLD[style.font]
        return style


def _style(preset: str, **kwargs) -> Style:
    font = kwargs.get("font", "Anton")
    kwargs.setdefault("bold", FONT_BOLD.get(font, False))
    return Style(preset=preset, **kwargs)


PRESETS: dict[str, Style] = {
    "hormozi": _style(
        "hormozi",
        font="Anton",
        font_size=104,
        text_color="#FFFFFF",
        highlight_color="#E6FF3A",
        position="bottom",
        words_per_line=3,
        uppercase=True,
        highlight_mode="active",
        boxed=False,
    ),
    "minimal": _style(
        "minimal",
        font="Inter",
        font_size=64,
        text_color="#FFFFFF",
        highlight_color="#FFFFFF",
        position="bottom",
        words_per_line=5,
        uppercase=False,
        highlight_mode="active",
        boxed=False,
    ),
    "karaoke": _style(
        "karaoke",
        font="Inter",
        font_size=72,
        text_color="#C8C2B8",
        highlight_color="#FFE566",
        position="middle",
        words_per_line=5,
        uppercase=False,
        highlight_mode="cumulative",
        boxed=False,
    ),
    "boxed": _style(
        "boxed",
        font="Inter",
        font_size=68,
        text_color="#FFFFFF",
        highlight_color="#E6FF3A",
        position="bottom",
        words_per_line=4,
        uppercase=False,
        highlight_mode="active",
        boxed=True,
    ),
}

PRESET_BLURBS = {
    "hormozi": "Big, uppercase, one word lit at a time. The look people mean when they say Submagic.",
    "minimal": "Smaller, mixed case, quiet. The words sit in the background instead of shouting.",
    "karaoke": "A whole line, and the words fill in as they are spoken.",
    "boxed": "White words on a dark pill. Easier to read on a busy background.",
}


def preset_style(name: str) -> Style:
    if name not in PRESETS:
        known = ", ".join(PRESETS)
        raise KeyError(f"Unknown preset {name!r}. Known presets: {known}.")
    # Copy so a caller can edit it without changing the preset itself.
    return Style.from_dict(PRESETS[name].to_dict())


def public_presets() -> list[dict]:
    items = []
    for name, style in PRESETS.items():
        item = style.to_dict()
        item["blurb"] = PRESET_BLURBS[name]
        items.append(item)
    return items
