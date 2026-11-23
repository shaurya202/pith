"""Shared fixtures for tests that need a real (tiny) video file."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pith.media import ffmpeg_exe


def make_video(
    path: Path,
    *,
    seconds: float = 2.0,
    size: str = "320x240",
    color: str = "black",
    audio: bool = True,
    video_filter: str | None = None,
) -> Path:
    command = [
        ffmpeg_exe(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:d={seconds}:r=25",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={seconds}"]
    if video_filter:
        command += ["-vf", video_filter]
    command += [
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path
