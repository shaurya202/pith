"""Pith: cut the pauses, keep your style.

A free, local alternative to Submagic. The pipeline is:

1. Transcribe the clip on this computer (pith.transcribe).
2. Decide which stretches are dead air (pith.silence).
3. Turn the words that remain into caption lines (pith.captions).
4. Render the kept stretches and burn the captions in (pith.export).

The window is a small local website (pith.server) opened by pith.cli.
"""

__version__ = "0.1.1"
