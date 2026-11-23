"""Entry point for the packaged app.

PyInstaller follows this import and bundles the pith package.
Fonts, the window, and the sample clip are added separately as data files.

A windowed Mac or Windows build has no console, so stdout and stderr
can be missing. Point them at the void before anything prints, or the
app dies on the first message.
"""

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from pith.cli import main

if __name__ == "__main__":
    main()
