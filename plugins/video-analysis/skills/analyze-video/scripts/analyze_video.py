#!/usr/bin/env python3
"""Analyze a video for fact-checking: diarized transcript, claims, and the visuals tied to them."""

import argparse
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(PLUGIN / "shared-video"), str(Path(__file__).resolve().parent)]
if __name__ == "__main__":
    # Runs under any python3 (macOS ships 3.9): re-exec into the plugin's own environment.
    from bootstrap import ensure_venv

    PYTHON = ensure_venv(PLUGIN / "requirements.lock")
    if Path(sys.prefix).resolve() != PYTHON.parents[1].resolve():
        os.execv(PYTHON, [str(PYTHON), __file__, *sys.argv[1:]])

from claims_spec import SPEC  # noqa: E402
from video_core import cli  # noqa: E402


def timecode_fps(value: str) -> float:
    # Checked before the paid request: a rate under 1 can't make HH:MM:SS:FF timecodes.
    fps = float(value)
    if not 1 <= fps <= 240:
        raise argparse.ArgumentTypeError("must be between 1 and 240")
    return fps


def main(argv: list[str] | None = None) -> int:
    built = cli.parser(__doc__, ".sessions/analyze-video")
    built[1].add_argument(
        "--fps",
        type=timecode_fps,
        help="Frame rate for timecodes, at least 1 (default: the video's own)",
    )
    return cli.main(argv, built, SPEC, lambda args: {"fps": getattr(args, "fps", None)})


if __name__ == "__main__":
    raise SystemExit(main())
