"""Build the short clips Pith uses to demo and to test.

The talking clip is a stand-in person (a circle and a shape, not a real
face) with speech and a few pauses. Long pauses are about a second.
Short ones are about a fifth of a second, under the default threshold,
so they should survive. The reference clip is eight hard cuts with a
yellow line of type at the top, which is enough for the style guesser
to grab onto.

Speech comes from espeak-ng when it is installed. The pictures are drawn
here, so nothing in this file needs a network.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from pith.errors import UserError
from pith.media import ffmpeg_exe
from pith.paths import fonts_dir, samples_dir

TALKING_NAME = "talking.mp4"
REFERENCE_NAME = "reference.mp4"

# (sentence, seconds of silence AFTER it). The first silence is the lead-in
# and uses an empty sentence.
SPEECH = [
    ("", 0.25),
    ("Hey.", 1.15),
    ("This is a sample clip for Pith.", 0.16),
    ("Short pauses should stay.", 1.20),
    ("Long pauses should get cut.", 0.14),
    ("Thanks for watching.", 0.35),
]


def _run(command: list[str]) -> None:
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise UserError(f"Could not build the sample clip. {tail}")


def _espeak() -> str:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        raise UserError(
            "espeak-ng is not installed, so Pith cannot synthesize the sample. "
            "Install espeak-ng, or run the app from a copy that already includes pith/samples/talking.mp4."
        )
    return binary


def _write_silence(path: Path, seconds: float, rate: int) -> None:
    _run(
        [
            ffmpeg_exe(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={rate}:cl=mono",
            "-t",
            f"{seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def _speech_wav(text: str, path: Path, rate: int) -> None:
    raw = path.with_suffix(".espeak.wav")
    proc = subprocess.run(
        [_espeak(), "-v", "en-us", "-s", "148", "-w", str(raw), text],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Some builds don't have the en-us voice. The default voice is fine.
        proc = subprocess.run(
            [_espeak(), "-s", "148", "-w", str(raw), text],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise UserError("espeak could not synthesize the sample script.")
    # espeak leaves its own silence around a sentence. Strip it so the
    # gaps we add below are the pauses the demo is about, not a surprise.
    _run(
        [
            ffmpeg_exe(),
            "-y",
            "-i",
            str(raw),
            "-af",
            (
                "silenceremove=start_periods=1:start_silence=0.02:start_threshold=-36dB:detection=peak,"
                "areverse,"
                "silenceremove=start_periods=1:start_silence=0.02:start_threshold=-36dB:detection=peak,"
                "areverse"
            ),
            "-ar",
            str(rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    raw.unlink(missing_ok=True)


def _concat_wavs(parts: list[Path], dest: Path) -> None:
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts), encoding="utf-8")
    _run(
        [
            ffmpeg_exe(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(dest),
        ]
    )
    listing.unlink(missing_ok=True)


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as handle:
        return handle.getnframes() / float(handle.getframerate())


def _person_frame(width: int = 720, height: int = 1280) -> np.ndarray:
    """A flat illustration. It exists so the sample is obviously a fixture."""

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (22, 19, 16)
    yy, xx = np.ogrid[:height, :width]
    cx, cy = width // 2, int(height * 0.34)
    head = (xx - cx) ** 2 + (yy - cy) ** 2 <= 118 ** 2
    frame[head] = (214, 176, 148)
    body = ((xx - cx) / 190.0) ** 2 + ((yy - int(height * 0.62)) / 250.0) ** 2 <= 1.0
    shirt = body & ~head
    frame[shirt] = (196, 78, 42)
    # A small floor line so the frame is not a flat void.
    frame[int(height * 0.9) :, :] = (28, 24, 20)
    return frame


def _write_ppm(path: Path, frame: np.ndarray) -> None:
    height, width, _ = frame.shape
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    path.write_bytes(header + frame.tobytes())


def build_talking(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / "_talking_build"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rate = 22050
    pieces: list[Path] = []
    for index, (text, pause) in enumerate(SPEECH):
        if text:
            wav = work / f"line-{index}.wav"
            _speech_wav(text, wav, rate)
            pieces.append(wav)
        if pause > 0:
            gap = work / f"gap-{index}.wav"
            _write_silence(gap, pause, rate)
            pieces.append(gap)
    audio = work / "speech.wav"
    _concat_wavs(pieces, audio)
    duration = _wav_seconds(audio)
    frame_path = work / "frame.ppm"
    _write_ppm(frame_path, _person_frame())
    _run(
        [
            ffmpeg_exe(),
            "-y",
            "-loop",
            "1",
            "-i",
            str(frame_path),
            "-i",
            str(audio),
            "-t",
            f"{duration:.3f}",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    shutil.rmtree(work, ignore_errors=True)
    return dest


def build_reference(dest: Path) -> Path:
    """Eight one-second shots, yellow type at the top, hard cuts between them."""

    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / "_reference_build"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    font = fonts_dir() / "Anton-Regular.ttf"
    colors = ["0b1c33", "6b2418", "123d28", "3a3010", "2a1848", "4a3018", "102028", "581c28"]
    parts = []
    for index, color in enumerate(colors):
        part = work / f"shot-{index}.mp4"
        _run(
            [
                ffmpeg_exe(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x{color}:s=720x1280:r=30:d=1",
                "-vf",
                (
                    f"drawtext=fontfile='{font.as_posix()}':text='LIKE THIS':"
                    "fontcolor=0xFFE14A:fontsize=78:x=(w-text_w)/2:y=148"
                ),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(part),
            ]
        )
        parts.append(part)
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts), encoding="utf-8")
    _run(
        [
            ffmpeg_exe(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    shutil.rmtree(work, ignore_errors=True)
    return dest


def talking_sample() -> Path:
    return samples_dir() / TALKING_NAME


def reference_sample() -> Path:
    return samples_dir() / REFERENCE_NAME


def ensure_talking_sample() -> Path:
    path = talking_sample()
    if path.exists() and path.stat().st_size > 1000:
        return path
    return build_talking(path)


def ensure_reference_sample() -> Path:
    path = reference_sample()
    if path.exists() and path.stat().st_size > 1000:
        return path
    return build_reference(path)
