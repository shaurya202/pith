"""Export: captions are really in the picture, and the beep stays with the flash."""

import subprocess
from pathlib import Path

import numpy as np

from pith.captions import build_ass, group_lines
from pith.export import build_filtergraph, export_video, filter_quote
from pith.media import ffmpeg_exe, probe, read_frames
from pith.paths import fonts_dir
from pith.presets import preset_style
from pith.project import Word
from pith.plan import build_plan
from tests.helpers import make_video


def _words(*triples):
    return [Word(index + 1, text, start, end) for index, (text, start, end) in enumerate(triples)]


def test_filter_quote_escapes_colons_and_spaces():
    quoted = filter_quote(Path("/tmp/a b/clip.ass"))
    assert quoted.startswith("'") and quoted.endswith("'")
    assert "a b" in quoted


def test_filtergraph_contains_the_keep_and_the_caption_file(tmp_path):
    ass = tmp_path / "captions.ass"
    ass.write_text("x", encoding="utf-8")
    graph = build_filtergraph(
        [(0.2, 0.6), (2.0, 2.5)],
        ass,
        fonts_dir(),
        "original",
        320,
        240,
        "preview",
    )
    assert "trim=start=0.200:end=0.600" in graph
    assert "trim=start=2.000:end=2.500" in graph
    assert "atrim=start=0.200:end=0.600" in graph
    assert "ass=" in graph


def test_burned_caption_is_visible_and_uppercase(tmp_path):
    source = make_video(tmp_path / "dark.mp4", seconds=2.0, size="320x240", color="0x14110e")
    words = _words(("hello", 0.3, 1.2))
    from pith.project import Project

    project = Project(
        id="abc123def456",
        name="t",
        source_name="dark.mp4",
        source_ext=".mp4",
        duration=2.0,
        width=320,
        height=240,
        fps=25,
        has_audio=True,
        created=0,
        updated=0,
        words=words,
        transcript_done=True,
        pause_threshold=0.4,
        padding=0.05,
        style=preset_style("hormozi"),
        aspect="original",
    )
    plan = build_plan(project, None)
    output = tmp_path / "out.mp4"
    summary = export_video(
        source,
        output,
        plan["_keeps"],
        plan["_spoken"],
        project.style,
        aspect="original",
        display_size=(320, 240),
        quality="preview",
    )
    assert output.exists()
    ass = output.with_suffix(".ass").read_text(encoding="utf-8")
    assert "HELLO" in ass
    info = probe(output)
    assert info["has_audio"]
    assert summary["width"] == 320
    assert abs(info["duration"] - (plan["_keeps"][0][1] - plan["_keeps"][0][0])) < 0.35
    # Read at the export's own size. Scaling an 18px caption into a
    # thumbnail blends the letters into the background.
    frames = read_frames(output, summary["width"], summary["height"], 8, 16)
    luma = [frame.astype(np.int16).sum(axis=2) / 3 for frame in frames]
    bright = max(int((channel > 170).sum()) for channel in luma)
    assert bright > 15
    assert bright < summary["width"] * summary["height"] * 0.4


def test_audio_flash_stays_in_sync_after_a_cut(tmp_path):
    source = tmp_path / "sync.mp4"
    binary = ffmpeg_exe()
    # A black clip, a white square at t=2.0, and a beep at the same moment.
    subprocess.run(
        [
            binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=4:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=36x36:d=0.24:r=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.24:sample_rate=48000",
            "-filter_complex",
            "[1:v]setpts=PTS+2/TB[sq];"
            "[0:v][sq]overlay=x=8:y=8:enable='between(t,2,2.24)'[v];"
            "[2:a]adelay=2000:all=1,apad=whole_dur=4[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    from pith.project import Project

    project = Project(
        id="abc123def456",
        name="sync",
        source_name="sync.mp4",
        source_ext=".mp4",
        duration=4.0,
        width=320,
        height=240,
        fps=25,
        has_audio=True,
        created=0,
        updated=0,
        words=_words(("hello", 0.2, 0.6), ("there", 2.0, 2.5)),
        transcript_done=True,
        pause_threshold=0.3,
        padding=0.0,
        style=preset_style("minimal"),
        aspect="original",
    )
    plan = build_plan(project, None)
    assert plan["stats"]["cut_seconds"] > 1.0
    output = tmp_path / "cut.mp4"
    export_video(
        source,
        output,
        plan["_keeps"],
        plan["_spoken"],
        project.style,
        aspect="original",
        display_size=(320, 240),
        quality="preview",
    )
    info = probe(output)
    assert info["duration"] < 1.6
    assert info["has_audio"]

    def peak_time(path: Path) -> float:
        raw = subprocess.check_output(
            [binary, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"]
        )
        samples = np.frombuffer(raw[: len(raw) - (len(raw) % 4)], dtype=np.float32)
        return float(np.argmax(np.abs(samples))) / 8000.0

    def flash_time(path: Path) -> float | None:
        frames = read_frames(path, 160, 120, 25, 120)
        for index, frame in enumerate(frames):
            corner = frame[:24, :24].astype(np.int16).mean()
            if corner > 80:
                return index / 25.0
        return None

    # AAC shifts the measured beep a little. What matters is that the cut
    # does not change the gap between the beep and the flash.
    source_flash = flash_time(source)
    output_flash = flash_time(output)
    assert source_flash is not None and output_flash is not None
    source_gap = peak_time(source) - source_flash
    output_gap = peak_time(output) - output_flash
    assert abs(output_gap - source_gap) < 0.12


def test_landscape_export_to_vertical_is_9_by_16(tmp_path):
    source = make_video(tmp_path / "wide.mp4", seconds=1.2, size="320x180", color="0x222222")
    from pith.project import Project

    project = Project(
        id="abc123def456",
        name="wide",
        source_name="wide.mp4",
        source_ext=".mp4",
        duration=1.2,
        width=320,
        height=180,
        fps=25,
        has_audio=True,
        created=0,
        updated=0,
        words=_words(("hi", 0.1, 1.0)),
        transcript_done=True,
        pause_threshold=0.4,
        padding=0.0,
        style=preset_style("boxed"),
        aspect="9:16",
    )
    plan = build_plan(project, None)
    output = tmp_path / "reel.mp4"
    summary = export_video(
        source,
        output,
        plan["_keeps"],
        plan["_spoken"],
        project.style,
        aspect="9:16",
        display_size=(320, 180),
        quality="preview",
    )
    assert (summary["width"], summary["height"]) == (540, 960)
    info = probe(output)
    assert info["width"] == 540 and info["height"] == 960
    ass = (output.with_suffix(".ass")).read_text(encoding="utf-8")
    style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))
    # BorderStyle is the 16th comma-separated field and 3 means "opaque box".
    assert style_line.split(",")[15] == "3"


def test_rotation_metadata_is_reported_and_export_is_upright(tmp_path):
    source = make_video(tmp_path / "side.mp4", seconds=1.0, size="320x240", color="red")
    data = source.read_bytes()
    identity = bytes.fromhex(
        "000100000000000000000000000000000001000000000000000000000000000040000000"
    )
    # a=0, b=1, c=-1, d=0. ffmpeg reports this as rotation of -90 degrees.
    rotated = bytes.fromhex(
        "000000000001000000000000FFFF00000000000000000000000000000000000040000000"
    )
    assert data.count(identity) >= 1
    # Only the track header should change. Replacing the first matrix is
    # the tkhd; a second copy (if any) is left alone.
    patched = tmp_path / "turned.mp4"
    patched.write_bytes(data.replace(identity, rotated, 1))
    info = probe(patched)
    assert info["rotation"] == -90
    assert (info["width"], info["height"]) == (240, 320)

    from pith.project import Project

    project = Project(
        id="abc123def456",
        name="turned",
        source_name="turned.mp4",
        source_ext=".mp4",
        duration=info["duration"],
        width=info["width"],
        height=info["height"],
        fps=25,
        has_audio=True,
        created=0,
        updated=0,
        words=_words(("hi", 0.1, 0.8)),
        transcript_done=True,
        padding=0.0,
        style=preset_style("minimal"),
        aspect="original",
    )
    plan = build_plan(project, None)
    output = tmp_path / "upright.mp4"
    summary = export_video(
        patched,
        output,
        plan["_keeps"],
        plan["_spoken"],
        project.style,
        aspect="original",
        display_size=(info["width"], info["height"]),
        quality="preview",
    )
    assert summary["height"] > summary["width"]
