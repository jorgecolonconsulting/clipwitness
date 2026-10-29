"""Pin a visual's onset to an exact frame with ffmpeg, inside the window Gemini reported.

Gemini says what the visual is, where it sits (box_2d) and roughly when it starts (within a few
frames in testing). Code then crops that box, splits a +/-1.5 s window into runs of changing frames,
and takes the first frame of the run that starts nearest Gemini's onset.
"""

import math
import subprocess
from fractions import Fraction
from pathlib import Path

WINDOW = 1.5
MAX_SHIFT = (
    1.0  # seconds; a change further from Gemini's onset is probably a different event
)
SIDE = 48  # crop is scaled to SIDE x SIDE grey; enough to see an element appear, cheap to diff
MIN_CHANGE = 2.0  # mean grey-level change (0-255) below which nothing visibly happened
# ponytail: fixed stillness threshold, calibrated on one clean render (slow drift ~0.1, onsets >0.3);
# measure it from the window's still frames if noisy (re-encoded/YouTube) sources misfire
STILL = 0.25  # mean grey-level change between consecutive frames that still counts as no motion


def native_fps(path: str) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(Fraction(out))


def timecode(seconds: float, fps: float) -> str:
    # ponytail: non-drop-frame; 29.97/59.94 drift ~3.6 s/hour vs wall clock, add drop-frame if needed
    nominal = round(fps)
    total = round(seconds * fps)
    secs, ff = divmod(total, nominal)
    return f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}:{ff:02d}"


def crop_filter(box: list[int]) -> str:
    """ffmpeg crop for a Gemini [ymin, xmin, ymax, xmax] 0-1000 box, padded 3%, full frame if degenerate."""
    y0, y1 = sorted(box[0::2])
    x0, x1 = sorted(box[1::2])
    y0, x0, y1, x1 = (
        max(0, y0 - 30),
        max(0, x0 - 30),
        min(1000, y1 + 30),
        min(1000, x1 + 30),
    )
    if (y1 - y0) * (x1 - x0) < 20000:  # under 2% of the frame: box is likely wrong
        y0, x0, y1, x1 = 0, 0, 1000, 1000
    return f"crop=iw*{(x1 - x0) / 1000}:ih*{(y1 - y0) / 1000}:iw*{x0 / 1000}:ih*{y0 / 1000}"


def region_frames(path: str, start: float, count: int, box: list[int]) -> list[bytes]:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start:.6f}",
            "-i",
            path,
            "-frames:v",
            str(count),
            "-vf",
            f"{crop_filter(box)},scale={SIDE}:{SIDE},format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    size = SIDE * SIDE
    return [raw[i : i + size] for i in range(0, len(raw) - size + 1, size)]


def mean_diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def motion_events(frames: list[bytes]) -> list[tuple[int, int]]:
    """(first, last) frame indexes of each run of consecutive changed frames that adds up to a visible change."""
    moving = [False] + [mean_diff(a, b) > STILL for a, b in zip(frames, frames[1:])]
    events, i = [], 1
    while i < len(frames):
        j = i
        while moving[i] and j + 1 < len(frames) and moving[j + 1]:
            j += 1
        if moving[i] and mean_diff(frames[i - 1], frames[j]) >= MIN_CHANGE:
            events.append((i, j))
        i = j + 1
    return events


def change_window(
    frames: list[bytes], near: float, max_shift: float
) -> tuple[int, int] | None:
    """(onset, settled) frame indexes of the motion event starting nearest frame `near` (Gemini's onset).

    Nearest, not biggest: a scene cut close by is often the biggest change in the box. None when the box
    shows no visible change, when the nearest event was already moving as the window opened, or when it
    starts more than `max_shift` frames from `near`.
    """
    events = motion_events(frames)
    if not events:
        return None
    onset, settled = min(events, key=lambda e: abs(e[0] - near))
    if onset == 1 or abs(onset - near) > max_shift:
        return None
    return onset, settled


def save_frame(path: str, seconds: float, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{seconds:.6f}",
            "-i",
            path,
            "-frames:v",
            "1",
            str(out),
        ],
        capture_output=True,
        check=True,
    )


def snap(path: str, visual, fps: float, out_dir: Path, name: str) -> None:
    """Move visual.onset_seconds onto the first changed frame and save a frame showing the visual.

    Keeps Gemini's timing (timing_source "gemini") when the box shows no clean onset in the window.
    `name` is a code-chosen file stem: visual ids come from the model and never become paths.
    Fields change only after the frame is saved, so a failure leaves the visual as Gemini gave it.
    """
    start = math.floor(max(0.0, visual.onset_seconds - WINDOW) * fps) / fps
    found = change_window(
        region_frames(path, start, round(2 * WINDOW * fps) + 1, visual.box_2d),
        (visual.onset_seconds - start) * fps,
        MAX_SHIFT * fps,
    )
    if found:
        onset, still = round(start + found[0] / fps, 3), round(
            start + found[1] / fps, 3
        )
    else:
        onset, still = visual.onset_seconds, round(
            (visual.onset_seconds + visual.end_seconds) / 2, 3
        )
    save_frame(path, still, out_dir / f"{name}.png")
    if not (
        out_dir / f"{name}.png"
    ).exists():  # ffmpeg exits 0 when seeking past the end
        raise FileNotFoundError(f"no frame at {still} s")
    visual.onset_seconds, visual.frame_seconds = onset, still
    visual.frame_path = f"frames/{name}.png"
    if found:
        visual.timing_source = "ffmpeg"
