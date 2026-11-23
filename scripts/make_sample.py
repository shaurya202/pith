"""Regenerate the talking-head sample and the reference reel.

The clips shipped in pith/samples are already built. Run this if you want
to make them again. It needs espeak-ng (or espeak) for the speech, and the
ffmpeg that Pith already installs.

    python scripts/make_sample.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pith.sample import build_reference, build_talking, reference_sample, talking_sample


def main() -> None:
    print("Building the talking sample…")
    talking = build_talking(talking_sample())
    print(f"  {talking}")
    print("Building the reference reel…")
    reference = build_reference(reference_sample())
    print(f"  {reference}")


if __name__ == "__main__":
    main()
