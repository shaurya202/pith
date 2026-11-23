"""End-to-end check: sample clip, Whisper, a cut, a burned-in export.

Unit tests cover the cut math without downloading a model. This command
is the one that proves a fresh install can actually finish a clip.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from pith.export import export_video
from pith.media import ffmpeg_exe, probe, read_frames, scaled_frame_size
from pith.plan import build_plan
from pith.project import Store
from pith.sample import ensure_talking_sample
from pith.transcribe import transcribe_file


def _bright_pixels(frame: np.ndarray) -> int:
    luma = frame.astype(np.int16).sum(axis=2) / 3
    return int((luma > 185).sum())


def run_self_test() -> int:
    print("Pith self-test")
    try:
        binary = ffmpeg_exe()
    except Exception as exc:
        print(f"FAIL  ffmpeg: {exc}")
        return 1
    print(f"  ffmpeg: {binary}")

    sample = ensure_talking_sample()
    info = probe(sample)
    print(f"  sample: {sample.name}  {info['duration']:.1f}s  {info['width']}x{info['height']}")
    if not info["has_audio"]:
        print("FAIL  sample has no audio")
        return 1

    print("  transcribing with the tiny model (first run downloads it)...")
    words, language = transcribe_file(sample, info["duration"], model_name="tiny")
    texts = " ".join(word.text for word in words)
    print(f"  language: {language}")
    print(f"  transcript: {texts}")
    if len(words) < 4:
        print(f"FAIL  expected several words, got {len(words)}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        project = store.create(sample, sample.name, info, [])
        project.words = words
        project.transcript_done = True
        project.pause_threshold = 0.40
        project.padding = 0.06
        project.aspect = "9:16"
        plan = build_plan(project, None, 0.02)
        print(
            f"  cuts: {plan['stats']['cut_count']}  "
            f"removing {plan['stats']['cut_seconds']:.2f}s  "
            f"keeping {plan['stats']['keep_seconds']:.2f}s"
        )
        gaps = [word.start - words[index].end for index, word in enumerate(words[1:], start=1)]
        long_gaps = [gap for gap in gaps if gap > 0.7]
        if long_gaps and plan["stats"]["cut_count"] < 1:
            print(f"FAIL  found pauses of {long_gaps} but nothing was cut")
            return 1
        output = Path(tmp) / "out.mp4"
        summary = export_video(
            store.source_path(project),
            output,
            plan["_keeps"],
            plan["_spoken"],
            project.style,
            aspect="9:16",
            display_size=(project.width, project.height),
            quality="preview",
        )
        print(f"  export: {summary['width']}x{summary['height']}  {summary['seconds']:.2f}s  {summary['bytes']} bytes")
        exported = probe(output)
        if not exported["has_audio"]:
            print("FAIL  export has no audio")
            return 1
        if abs(exported["duration"] - summary["seconds"]) > 0.4:
            print("FAIL  reported duration does not match the file")
            return 1
        if summary["width"] != 540 or summary["height"] != 960:
            print("FAIL  9:16 preview should be 540x960")
            return 1
        width, height = scaled_frame_size(exported["width"], exported["height"], 180)
        frames = read_frames(output, width, height, 4, 12)
        if not frames:
            print("FAIL  could not read the export")
            return 1
        brightest = max(_bright_pixels(frame) for frame in frames)
        # The sample background is dark. Burned-in white captions should
        # light up some pixels, but not the whole frame.
        if brightest < 20:
            print(f"FAIL  no caption-like bright pixels in the export (max {brightest})")
            return 1
        ratio = brightest / float(frames[0].size / 3)
        if ratio > 0.5:
            print("FAIL  the frame is mostly white, which is not a caption")
            return 1
    print("PASS")
    return 0
