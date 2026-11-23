"""Talking to ffmpeg.

Pith ships its own ffmpeg through imageio-ffmpeg so a download does not
depend on a system install. If that binary is missing, we look for ffmpeg
on PATH. Either way it has to include libx264 (the video codec) and libass
(the caption renderer). Those are checked once and then remembered.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

from pith.errors import UserError

_FFMPEG: str | None = None


def _supports(binary: str) -> bool:
    try:
        proc = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = proc.stdout + proc.stderr
    if not re.search(r"\bass\b", text):
        return False
    try:
        encoders = subprocess.run(
            [binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "libx264" in encoders.stdout + encoders.stderr


def ffmpeg_exe() -> str:
    """Path to an ffmpeg that can encode H.264 and burn ASS captions."""

    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    candidates: list[str] = []
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found and found not in candidates:
        candidates.append(found)
    for candidate in candidates:
        if candidate and _supports(candidate):
            _FFMPEG = candidate
            return candidate
    raise UserError(
        "Pith could not find a usable ffmpeg. Reinstall with pip so imageio-ffmpeg "
        "is present, or install ffmpeg with libx264 and libass and put it on PATH."
    )


def ffmpeg_version() -> str:
    try:
        binary = ffmpeg_exe()
    except UserError as exc:
        return exc.message
    proc = subprocess.run([binary, "-version"], capture_output=True, text=True)
    line = (proc.stdout or proc.stderr or "").splitlines()
    return line[0] if line else binary


def probe(path: Path) -> dict:
    """Duration, display size, fps, audio, and rotation.

    Phone videos are often stored sideways, with a display matrix that
    says how to turn them upright. ffmpeg applies that turn automatically
    before our filters (we checked), so the filter graph already sees the
    upright frame. We still read the angle so the window and the export
    size use the upright dimensions rather than the stored ones.
    """

    binary = ffmpeg_exe()
    proc = subprocess.run(
        [binary, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    text = proc.stderr or ""
    if "Duration:" not in text or "Video:" not in text:
        raise UserError("That file does not look like a video Pith can read. Use an mp4 or mov.")
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not duration_match:
        raise UserError("Pith could not read the length of this video.")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    video_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if not video_match:
        raise UserError("Pith could not read the picture size of this video.")
    stored_w, stored_h = int(video_match.group(1)), int(video_match.group(2))
    rotation = 0.0
    rotation_match = re.search(r"rotation of ([+-]?\d+(?:\.\d+)?) degrees", text, re.I)
    if rotation_match:
        rotation = float(rotation_match.group(1))
    else:
        tag = re.search(r"rotate\s*:\s*([+-]?\d+(?:\.\d+)?)", text, re.I)
        if tag:
            rotation = float(tag.group(1))
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    fps = float(fps_match.group(1)) if fps_match else 30.0
    if fps <= 1 or fps > 120:
        fps = 30.0
    display_w, display_h = display_size(stored_w, stored_h, rotation)
    return {
        "duration": duration,
        "width": display_w,
        "height": display_h,
        "stored_width": stored_w,
        "stored_height": stored_h,
        "rotation": rotation,
        "fps": fps,
        "has_audio": "Audio:" in text,
    }


def display_size(width: int, height: int, rotation: float) -> tuple[int, int]:
    turns = int(round(rotation)) % 360
    if turns in (90, 270):
        return height, width
    return width, height


def audio_features(path: Path, buckets: int = 200, sample_rate: int = 16000, hop_s: float = 0.02):
    """Waveform peaks for the timeline, and a volume envelope for pause detection.

    Returns (peaks, rms, hop_seconds). Peaks are 0..1 and safe to store in
    the project file. `rms` is the raw volume of each short slice.
    """

    binary = ffmpeg_exe()
    proc = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout:
        return [], np.zeros(0, dtype=np.float32), hop_s
    raw = proc.stdout
    raw = raw[: len(raw) - (len(raw) % 4)]
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    if samples.size == 0:
        return [], np.zeros(0, dtype=np.float32), hop_s
    hop = max(1, int(sample_rate * hop_s))
    usable = samples.size // hop
    if usable == 0:
        peak = float(np.max(np.abs(samples))) or 1.0
        return [round(float(np.max(np.abs(samples))) / peak, 4)], np.zeros(0, dtype=np.float32), hop_s
    frames = samples[: usable * hop].reshape(usable, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1)).astype(np.float32)
    chunk = max(1, int(np.ceil(samples.size / buckets)))
    peaks = []
    for index in range(0, samples.size, chunk):
        peaks.append(float(np.max(np.abs(samples[index : index + chunk]))))
        if len(peaks) >= buckets:
            break
    loudest = max(peaks) or 1.0
    return [round(peak / loudest, 4) for peak in peaks], rms, hop_s


def scaled_frame_size(display_w: int, display_h: int, width: int = 160) -> tuple[int, int]:
    if display_w <= 0 or display_h <= 0:
        return width, width
    height = int(round(display_h * (width / float(display_w))))
    if height % 2:
        height += 1
    return width, max(2, height)


def read_frames(path: Path, width: int, height: int, sample_fps: float, max_frames: int):
    """RGB frames, evenly sampled. Used by the reference matcher."""

    binary = ffmpeg_exe()
    proc = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={sample_fps},scale={width}:{height}",
            "-frames:v",
            str(max_frames),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    frame_bytes = width * height * 3
    if proc.returncode != 0 or frame_bytes <= 0 or not proc.stdout:
        detail = (proc.stderr or b"").decode("utf-8", "replace")[-400:]
        raise UserError(f"Could not read frames from that video. {detail}".strip())
    count = len(proc.stdout) // frame_bytes
    frames = []
    for index in range(count):
        chunk = proc.stdout[index * frame_bytes : (index + 1) * frame_bytes]
        frames.append(np.frombuffer(chunk, dtype=np.uint8).reshape((height, width, 3)).copy())
    return frames


def even(value: float) -> int:
    number = int(round(value))
    if number % 2:
        number -= 1
    return max(2, number)


def output_size(display_w: int, display_h: int, aspect: str, quality: str) -> tuple[int, int]:
    """Pixel size of the exported file.

    9:16 is a center crop scaled to a reel frame. "original" keeps the
    upright aspect and only shrinks when a side is longer than the cap.
    Preview uses a smaller cap so a render comes back quickly.
    """

    if aspect == "9:16":
        return (540, 960) if quality == "preview" else (1080, 1920)
    long_side = 720 if quality == "preview" else 1920
    longest = max(display_w, display_h, 1)
    scale = min(1.0, long_side / float(longest))
    return even(display_w * scale), even(display_h * scale)
