"""Probe text from ffmpeg, including a phone-style rotation matrix."""

from pith.media import display_size, probe


BANNER = """
Input #0, mov,mp4, from 'clip.mov':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 1200 kb/s
  Stream #0:0(und): Video: h264 (High), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 25 fps, 25 tbr, 12800 tbn
    Side data:
      Display Matrix: rotation of -90.00 degrees
  Stream #0:1(und): Audio: aac (LC), 48000 Hz, stereo, fltp, 128 kb/s
"""


def test_display_size_swaps_on_quarter_turns():
    assert display_size(1920, 1080, -90) == (1080, 1920)
    assert display_size(1920, 1080, 90) == (1080, 1920)
    assert display_size(1920, 1080, 180) == (1920, 1080)
    assert display_size(1920, 1080, 0) == (1920, 1080)


def test_probe_parser_reads_rotation(monkeypatch, tmp_path):
    video = tmp_path / "clip.mov"
    video.write_bytes(b"not really")

    class Result:
        stderr = BANNER
        stdout = ""
        returncode = 1

    monkeypatch.setattr("pith.media.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("pith.media.ffmpeg_exe", lambda: "ffmpeg")
    info = probe(video)
    assert info["duration"] == 62.5
    assert (info["stored_width"], info["stored_height"]) == (1920, 1080)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["rotation"] == -90
    assert info["fps"] == 25
    assert info["has_audio"] is True
