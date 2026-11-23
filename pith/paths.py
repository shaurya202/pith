"""Where Pith keeps its own files, and where it keeps your projects."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def package_dir() -> Path:
    """The folder that holds this package's fonts, samples, and UI.

    A PyInstaller build unpacks those next to the code under sys._MEIPASS.
    Running from source just uses the pith/ directory itself.
    """

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "pith"
    return Path(__file__).resolve().parent


def fonts_dir() -> Path:
    return package_dir() / "fonts"


def static_dir() -> Path:
    return package_dir() / "static"


def samples_dir() -> Path:
    return package_dir() / "samples"


def default_data_dir() -> Path:
    override = os.environ.get("PITH_DATA")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pith"
