"""Build a double-clickable Pith app with PyInstaller.

PyInstaller cannot cross-compile, and the speech libraries ship as
native wheels. Run this on the OS you want a download for:

    pip install -r requirements.txt -r requirements-dev.txt
    python scripts/package.py

What you get, in dist/:

- macOS: Pith.app inside Pith-macOS-<arch>.dmg and the same app as a zip
- Windows: Pith-Windows-x64.zip with Pith.exe inside
- Linux: Pith-Linux-<arch>.zip

The first transcription still downloads a Whisper model. ffmpeg is inside
the build. The app is unsigned.

macOS Gatekeeper: right-click Pith, choose Open, then Open again.
Windows SmartScreen: choose More info, then Run anyway.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTALL_TEXT = """Pith

Double-click Pith to open it. ffmpeg is already inside this download.
The first transcription downloads a Whisper model (base is about 150 MB)
into your user folder. After that, transcription works offline.

macOS
  Finder may say Pith is from an unidentified developer, because this
  build is not signed with an Apple certificate. Right-click Pith, choose
  Open, then Open again. That is the Gatekeeper prompt.

Windows
  SmartScreen may say "Windows protected your PC". Choose More info, then
  Run anyway. That warning is about the missing code signature.
  If the desktop window does not open, Pith opens in your browser.
  Windows 10 needs the Evergreen WebView2 runtime for the desktop window.
  Windows 11 already has it.
"""


def artifact_stem() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"Pith-macOS-{arch}"
    if system == "Windows":
        arch = "x64" if machine in {"amd64", "x86_64"} else machine
        return f"Pith-Windows-{arch}"
    arch = "x86_64" if machine in {"x86_64", "amd64"} else machine
    return f"Pith-Linux-{arch}"


def _windowed() -> bool:
    override = os.environ.get("PITH_WINDOWED")
    if override == "1":
        return True
    if override == "0":
        return False
    # A Mac or Windows download should open without a terminal.
    # The Linux build keeps a console so a launch log is visible.
    return platform.system() in {"Darwin", "Windows"}


def _drop_unused(root: Path) -> None:
    """The ONNX transformer toolkit is not used to transcribe or export.

    It is pulled in by a full collect of onnxruntime and is safe to omit.
    The server still starts without it.
    """

    for folder in root.rglob("transformers"):
        if folder.is_dir() and folder.parent.name == "onnxruntime":
            shutil.rmtree(folder)


def _tar_xz(source: Path, dest: Path) -> Path:
    import tarfile

    if dest.exists():
        dest.unlink()
    with tarfile.open(dest, "w:xz") as archive:
        archive.add(source, arcname=source.name)
    return dest


def _zip(source: Path, dest_stem: Path) -> Path:
    if dest_stem.with_suffix(".zip").exists():
        dest_stem.with_suffix(".zip").unlink()
    archive = shutil.make_archive(str(dest_stem), "zip", root_dir=source.parent, base_dir=source.name)
    return Path(archive)


def _make_dmg(folder: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    subprocess.check_call(
        [
            "hdiutil",
            "create",
            "-volname",
            "Pith",
            "-srcfolder",
            str(folder),
            "-ov",
            "-format",
            "UDZO",
            str(dest),
        ]
    )


def build() -> Path:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("Install PyInstaller first: pip install pyinstaller")

    separator = ";" if platform.system() == "Windows" else ":"
    windowed = _windowed()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "Pith",
        "--paths",
        str(ROOT),
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
        "--collect-all",
        "av",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols",
        "--hidden-import",
        "uvicorn.protocols.http",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan",
        "--hidden-import",
        "uvicorn.lifespan.on",
        "--copy-metadata",
        "imageio-ffmpeg",
        "--copy-metadata",
        "faster-whisper",
        "--copy-metadata",
        "huggingface-hub",
    ]
    if windowed:
        command.append("--windowed")
    if platform.system() == "Darwin":
        command.extend(["--osx-bundle-identifier", "app.pith.desktop"])
    for source, dest in (
        ("pith/static", "pith/static"),
        ("pith/fonts", "pith/fonts"),
        ("pith/samples", "pith/samples"),
    ):
        command.extend(["--add-data", f"{ROOT / source}{separator}{dest}"])
    command.append(str(ROOT / "scripts" / "pith_entry.py"))
    print(" ".join(command))
    subprocess.check_call(command, cwd=ROOT)

    stem = artifact_stem()
    dist = ROOT / "dist"
    notes = dist / "INSTALL.txt"
    notes.write_text(INSTALL_TEXT, encoding="utf-8")

    if platform.system() == "Darwin" and windowed:
        app = dist / "Pith.app"
        _drop_unused(app)
        binary = app / "Contents" / "MacOS" / "Pith"
        stage = dist / "dmg-stage"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        shutil.copytree(app, stage / "Pith.app", symlinks=True)
        shutil.copy(notes, stage / "INSTALL.txt")
        dmg = dist / f"{stem}.dmg"
        _make_dmg(stage, dmg)
        archive = _zip(stage, dist / stem)
        produced = dmg
    else:
        folder = dist / "Pith"
        _drop_unused(folder)
        binary = folder / ("Pith.exe" if platform.system() == "Windows" else "Pith")
        shutil.copy(notes, folder / "INSTALL.txt")
        archive = _zip(folder, dist / stem)
        produced = archive
        if platform.system() == "Linux":
            packed = _tar_xz(folder, dist / f"{stem}.tar.xz")
            print(f"tarball: {packed}")

    (dist / "pith-binary.path").write_text(str(binary.resolve()), encoding="utf-8")
    print(f"binary: {binary}")
    print(f"archive: {archive}")
    if produced != archive:
        print(f"disk image: {produced}")
    return produced


if __name__ == "__main__":
    build()
