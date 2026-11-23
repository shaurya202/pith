"""Build a double-clickable Pith app with PyInstaller.

Run this on the operating system you want to ship. A build made on
Windows only runs on Windows, and the same is true for Mac and Linux.

    pip install -r requirements-dev.txt
    python scripts/package.py

The result is dist/Pith/ (an .exe on Windows, a binary on Linux, and on
Mac you can wrap that folder as Pith.app). The Whisper model is not
inside the build. The first transcription downloads it.

On Mac, Gatekeeper will warn about an unsigned app. Right-click Pith,
choose Open, then Open again.

On Windows, SmartScreen may say the app is unrecognized. Choose More
info, then Run anyway. That warning is about the missing code signature,
not about what Pith does.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("Install PyInstaller first: pip install pyinstaller")

    separator = ";" if platform.system() == "Windows" else ":"
    bundles = [
        ("pith/static", "pith/static"),
        ("pith/fonts", "pith/fonts"),
        ("pith/samples", "pith/samples"),
    ]
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "Pith",
        "--windowed",
        "--collect-all",
        "faster_whisper",
        "--collect-all",
        "ctranslate2",
        "--collect-all",
        "imageio_ffmpeg",
        "--collect-all",
        "tokenizers",
        "--collect-all",
        "onnxruntime",
    ]
    for source, dest in bundles:
        path = ROOT / source
        if not path.exists():
            print(f"Skipping missing bundle: {source}")
            continue
        command.extend(["--add-data", f"{path}{separator}{dest}"])
    command.append(str(ROOT / "pith" / "cli.py"))
    print(" ".join(command))
    subprocess.check_call(command, cwd=ROOT)
    print("Built dist/Pith. The first transcription still downloads a Whisper model.")


if __name__ == "__main__":
    main()
