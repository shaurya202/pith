"""Cut the kept stretches together and burn the captions in.

Video and audio are trimmed with the same timestamps and concatenated,
so they stay in lockstep. Captions are drawn after that, in the output
timeline. Doing the captions first and cutting second would slice a
word in half and leave the highlight pointing at the wrong moment.

ffmpeg autorotates phone video before the filter graph on the build we
ship, so a portrait clip stored sideways comes out upright. We size the
frame from the upright dimensions (see pith.media.probe) and do not
rotate a second time.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from pith.captions import build_ass, group_lines
from pith.errors import UserError
from pith.media import ffmpeg_exe, output_size, probe
from pith.paths import fonts_dir

log = logging.getLogger("pith")

# ffmpeg writes progress here. A second export must not share the pipe,
# but each call has its own process, so a module-level handle is only
# used so the process can be stopped if the app exits mid-render.
_process_lock = threading.Lock()
_current: subprocess.Popen | None = None


def stop_ffmpeg() -> None:
    global _current
    with _process_lock:
        proc = _current
        _current = None
    if proc is not None and proc.poll() is None:
        proc.kill()


def filter_quote(path: Path) -> str:
    """Quote a path so the filter graph does not split on colons or commas.

    The quotes are for ffmpeg, not for the shell. The whole graph is one
    argument. Colons still have to be escaped because they separate options
    even inside quotes (this matters for Windows paths like C:\\...).
    """

    text = path.resolve().as_posix().replace("\\", "/")
    text = text.replace("\\", r"\\")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    text = text.replace(",", r"\,")
    text = text.replace("[", r"\[")
    text = text.replace("]", r"\]")
    return f"'{text}'"


def build_filtergraph(keeps, ass_path: Path | None, fonts: Path, aspect: str, out_w: int, out_h: int, quality: str) -> str:
    if not keeps:
        raise UserError("Everything is marked to cut. Put a pause back, or restore a word, then export.")
    parts = []
    for index, (start, end) in enumerate(keeps):
        start_s = f"{start:.3f}"
        end_s = f"{end:.3f}"
        parts.append(f"[0:v]trim=start={start_s}:end={end_s},setpts=PTS-STARTPTS[v{index}]")
        parts.append(
            f"[0:a]atrim=start={start_s}:end={end_s},asetpts=PTS-STARTPTS,aresample=48000[a{index}]"
        )
    joined = "".join(f"[v{index}][a{index}]" for index in range(len(keeps)))
    parts.append(f"{joined}concat=n={len(keeps)}:v=1:a=1[vc][ac]")
    filters = []
    if aspect == "9:16":
        # Center crop. Commas inside the expressions are escaped so they
        # are not read as the next filter.
        filters.append("crop=x=(iw-ow)/2:y=(ih-oh)/2:w=min(iw\\,ih*9/16):h=min(ih\\,iw*16/9)")
    flags = "bilinear" if quality == "preview" else "lanczos"
    filters.append(f"scale={int(out_w)}:{int(out_h)}:flags={flags}")
    if ass_path is not None:
        filters.append(f"ass={filter_quote(ass_path)}:fontsdir={filter_quote(fonts)}")
    filters.append("format=yuv420p")
    parts.append("[vc]" + ",".join(filters) + "[vout]")
    return ";".join(parts)


def _parse_out_time(line: str):
    # out_time=00:00:01.234567
    if not line.startswith("out_time="):
        return None
    stamp = line.split("=", 1)[1].strip()
    if stamp in {"", "N/A"}:
        return None
    try:
        clock, fraction = stamp.split(".")
        hours, minutes, seconds = clock.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction[:2]) / 100
    except ValueError:
        return None


def export_video(
    source: Path,
    output: Path,
    keeps: list[tuple[float, float]],
    spoken: list[dict],
    style,
    *,
    aspect: str,
    display_size: tuple[int, int],
    quality: str = "full",
    on_progress=None,
) -> dict:
    """Write `output` and a sibling .ass file. Returns a small summary."""

    if not keeps:
        raise UserError("Everything is marked to cut. Put a pause back, or restore a word, then export.")
    width, height = output_size(display_size[0], display_size[1], aspect, quality)
    lines = group_lines(spoken, style.words_per_line) if spoken else []
    ass_path = output.with_suffix(".ass")
    ass_text = build_ass(style, lines, width, height) if lines else None
    if ass_text:
        ass_path.write_text(ass_text, encoding="utf-8")
    graph = build_filtergraph(
        keeps,
        ass_path if ass_text else None,
        fonts_dir(),
        aspect,
        width,
        height,
        quality,
    )
    preset = "ultrafast" if quality == "preview" else "medium"
    crf = "26" if quality == "preview" else "18"
    expected = sum(end - start for start, end in keeps) or 0.01
    command = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-map",
        "[ac]",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    log.info("export %s -> %s (%s)", source.name, output.name, quality)
    log.debug("filter %s", graph)
    error_path = output.with_suffix(".ffmpeg.log")
    global _current
    with error_path.open("w", encoding="utf-8") as error_file:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=error_file,
            text=True,
        )
        with _process_lock:
            _current = proc
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                stamp = _parse_out_time(line.strip())
                if stamp is not None and on_progress:
                    on_progress(min(0.99, stamp / expected), "Rendering")
            code = proc.wait(timeout=max(120, expected * 40))
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise UserError("The export took too long and was stopped.") from exc
        finally:
            with _process_lock:
                if _current is proc:
                    _current = None
    if code != 0 or not output.exists() or output.stat().st_size < 1000:
        tail = error_path.read_text(encoding="utf-8", errors="replace")[-1200:]
        log.error("ffmpeg failed: %s", tail)
        raise UserError("Export failed. The last lines from ffmpeg: " + tail[-500:])
    info = probe(output)
    if on_progress:
        on_progress(1.0, "Done")
    return {
        "seconds": round(info["duration"], 2),
        "width": info["width"],
        "height": info["height"],
        "bytes": output.stat().st_size,
        "quality": quality,
        "aspect": aspect,
    }
