import json
import shutil
import subprocess
from pathlib import Path

import analyze_video
import claims_spec
import frames
import pytest
from pydantic import ValidationError
from video_core import cli
from video_core.contracts import InteractionResult, PreparedVideo

FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def result(**visual):
    return {
        "summary": "Aquifers",
        "transcript": [
            {
                "speaker": "Narrator",
                "start_seconds": 0.2,
                "end_seconds": 2.5,
                "text": "An aquifer holds water.",
            },
        ],
        "claims": [
            {
                "id": "c1",
                "quote": "An aquifer holds water.",
                "speaker": "Narrator",
                "source": "spoken",
                "start_seconds": 0.2,
                "end_seconds": 2.5,
            },
        ],
        "visuals": [
            {
                "id": "v1",
                "claim_ids": ["c1"],
                "kind": "animation",
                "description": "A square appears",
                "on_screen_text": "",
                "box_2d": [250, 250, 750, 750],
                "onset_seconds": 1.2,
                "end_seconds": 2.5,
                "timing_confidence": "medium",
                **visual,
            },
        ],
        "uncertainties": [],
    }


class Provider:
    timeout_seconds = 0.02

    def __init__(self, text):
        self.text = text
        self.levels = []
        self.models = []

    def prepare(self, source, on_upload=None):
        return PreparedVideo(
            uri=source.uri or "https://files/local", mime_type="video/mp4"
        )

    def submit(
        self,
        video,
        prompt,
        instructions,
        model,
        previous_id,
        schema,
        thinking_level=None,
    ):
        assert "box_2d" in json.dumps(schema)
        self.levels.append(thinking_level)
        self.models.append(model)
        return "id-1"

    def retrieve(self, interaction_id):
        return InteractionResult(id=interaction_id, status="completed", text=self.text)


@pytest.fixture
def clip(tmp_path):
    """3 s of grey at 30 fps; a white square appears at frame 45 (1.5 s) while a small dot drifts."""
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=320x240:r=30:d=3",
            "-vf",
            "drawbox=x=90+t*4:y=60:w=6:h=6:color=black:t=fill,"
            "drawbox=x=110:y=70:w=100:h=100:color=white:t=fill:enable='gte(n,45)'",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def run(tmp_path, monkeypatch, text, source, *extra):
    monkeypatch.setattr(cli, "create_provider", lambda timeout: Provider(text))
    code = analyze_video.main(
        [
            "--session-root",
            str(tmp_path / "s"),
            "analyze",
            source,
            "--session",
            "demo",
            *extra,
        ]
    )
    return code, tmp_path / "s" / "demo" / "revision-0001"


def test_visual_linked_to_missing_claim_is_rejected():
    with pytest.raises(ValidationError, match="unknown claims"):
        claims_spec.VideoClaims.model_validate(result(claim_ids=["c9"]))


def test_change_window_takes_the_event_nearest_gemini_not_the_biggest():
    still = bytes(100)
    drift = [
        bytes([i // 10] * 5) + bytes(95) for i in range(20)
    ]  # slow change: not an event
    label = [bytes([0] * 90 + [60] * 10)] * 10  # small label appears at 20
    cut = [bytes([200] * 100)] * 10  # scene cut at 30: the biggest change
    assert frames.change_window(drift + label + cut, near=22, max_shift=30) == (20, 20)
    assert frames.change_window(drift + label + cut, near=29, max_shift=30) == (30, 30)
    assert frames.change_window([still] * 10, near=5, max_shift=30) is None
    assert frames.change_window(drift + label, near=5, max_shift=10) is None  # too far
    ramp = [bytes([i * 10] * 100) for i in range(10)]
    assert (
        frames.change_window(ramp, near=5, max_shift=30) is None
    )  # already moving as the window opens


def test_timecode_counts_frames_at_the_given_rate():
    assert frames.timecode(1.5, 30) == "00:00:01:15"
    assert frames.timecode(3661.04, 25) == "01:01:01:01"


@FFMPEG
def test_local_video_onset_snaps_to_the_exact_frame(tmp_path, monkeypatch, clip):
    code, revision = run(
        tmp_path, monkeypatch, json.dumps(result()), str(clip), "--fps", "24"
    )
    assert code == 0
    visual = json.loads((revision / "analysis.json").read_text())["analysis"][
        "visuals"
    ][0]
    assert visual["timing_source"] == "ffmpeg"
    assert visual["onset_seconds"] == 1.5 and visual["gemini_onset_seconds"] == 1.2
    assert visual["onset_tc"] == "00:00:01:12"  # 1.5 s at the requested 24 fps
    assert visual["frame_path"] == "frames/visual-001.png"
    assert (revision / visual["frame_path"]).stat().st_size > 0


@FFMPEG
def test_model_chosen_ids_never_become_paths(tmp_path, monkeypatch, clip):
    code, revision = run(
        tmp_path, monkeypatch, json.dumps(result(id="../../escape")), str(clip)
    )
    assert code == 0
    assert not (tmp_path / "escape.png").exists()
    assert list((revision / "frames").iterdir()) == [
        revision / "frames" / "visual-001.png"
    ]


@FFMPEG
def test_failed_still_keeps_gemini_timing(tmp_path, monkeypatch, clip):
    def fail(*args):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(frames, "save_frame", fail)
    code, revision = run(tmp_path, monkeypatch, json.dumps(result()), str(clip))
    assert code == 0
    visual = json.loads((revision / "analysis.json").read_text())["analysis"][
        "visuals"
    ][0]
    assert (visual["timing_source"], visual["onset_seconds"]) == ("gemini", 1.2)


def test_fps_below_one_is_rejected_before_any_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "create_provider", lambda timeout: pytest.fail("submitted")
    )
    with pytest.raises(SystemExit):
        analyze_video.main(
            [
                "analyze",
                "https://youtu.be/dQw4w9WgXcQ",
                "--session",
                "d",
                "--fps",
                "0.1",
            ]
        )


def test_youtube_keeps_gemini_timing_and_writes_a_transcript_fact_check_reads(
    tmp_path, monkeypatch
):
    code, revision = run(
        tmp_path, monkeypatch, json.dumps(result()), "https://youtu.be/dQw4w9WgXcQ"
    )
    assert code == 0
    visual = json.loads((revision / "analysis.json").read_text())["analysis"][
        "visuals"
    ][0]
    assert (visual["timing_source"], visual["onset_tc"], visual["frame_path"]) == (
        "gemini",
        "00:00:01:06",
        None,
    )
    assert "Narrator: An aquifer holds water." in (
        revision / "transcript.srt"
    ).read_text().splitlines()


def test_ffmpeg_failure_keeps_the_paid_analysis(tmp_path, monkeypatch):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"not a video")
    code, revision = run(tmp_path, monkeypatch, json.dumps(result()), str(video))
    assert code == 0
    analysis = json.loads((revision / "analysis.json").read_text())["analysis"]
    assert analysis["visuals"][0]["timing_source"] == "gemini"
    assert "Gemini timing kept" in analysis["processing_notes"][0]
    assert analysis["uncertainties"] == []  # Gemini's doubts stay Gemini's


def test_srt_keeps_numbers_on_their_own_line_as_text():
    # A bare "2020" line inside a cue would be read as a cue index by SRT parsers.
    segment = claims_spec.Segment(
        speaker="A", start_seconds=0, end_seconds=1, text="Since\n2020\n\nprices rose."
    )
    assert "A: Since 2020 prices rose." in claims_spec.srt([segment]).splitlines()


@FFMPEG
def test_unexpected_frame_errors_and_missing_stills_keep_the_analysis(
    tmp_path, monkeypatch, clip
):
    late = json.dumps(result(onset_seconds=9, end_seconds=9.5))
    code, revision = run(tmp_path, monkeypatch, late, str(clip))
    assert code == 0  # onset past the 3 s clip: no still can be saved
    visual = json.loads((revision / "analysis.json").read_text())["analysis"][
        "visuals"
    ][0]
    assert (visual["timing_source"], visual["frame_path"]) == ("gemini", None)
    monkeypatch.setattr(frames, "native_fps", lambda path: 1 / 0)
    code, revision = run(tmp_path / "b", monkeypatch, json.dumps(result()), str(clip))
    assert code == 0


@pytest.mark.parametrize(
    "flags, model, level",
    [
        ([], "gemini-3.5-flash-lite", "high"),
        (["--depth", "deep"], "gemini-3.8-flash", "medium"),
        # Lite defaults to minimal thinking, which skims whole scenes; high keeps it agentic.
        (["--depth", "lite"], "gemini-3.5-flash-lite", "high"),
        (["--depth", "lite", "--thinking-level", "medium"], "gemini-3.5-flash-lite", "medium"),
        (["--depth", "lite", "--model", "m"], "m", "high"),
    ],
)
def test_depth_picks_model_and_thinking_kept_for_refinements(
    tmp_path, monkeypatch, flags, model, level
):
    provider = Provider(json.dumps(result()))
    monkeypatch.setattr(cli, "create_provider", lambda timeout: provider)
    run = lambda *argv: analyze_video.main(["--session-root", str(tmp_path), *argv])
    assert run("analyze", "https://youtu.be/dQw4w9WgXcQ", "--session", "d", *flags) == 0
    assert run("refine", "--session", "d", "--prompt", "Look again") == 0
    assert (provider.models, provider.levels) == ([model] * 2, [level] * 2)


@FFMPEG
def test_replaced_video_never_supplies_stills_for_a_followup(tmp_path, monkeypatch, clip):
    # Refinements reuse Gemini's copy; frames from a different file at the same path would mislabel evidence.
    code, _ = run(tmp_path, monkeypatch, json.dumps(result()), str(clip))
    assert code == 0
    other = tmp_path / "other.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-crf", "35", str(other)], check=True
    )
    other.replace(clip)  # same onset, different bytes: snapping would still "work"
    code = analyze_video.main(
        ["--session-root", str(tmp_path / "s"), "refine", "--session", "demo", "--prompt", "Again"]
    )
    assert code == 0
    analysis = json.loads(
        (tmp_path / "s" / "demo" / "revision-0002" / "analysis.json").read_text()
    )["analysis"]
    assert analysis["visuals"][0]["timing_source"] == "gemini"
    assert analysis["visuals"][0]["frame_path"] is None
    assert "changed since it was uploaded" in analysis["processing_notes"][0]


def test_reversed_ranges_publish_as_valid_subtitles(tmp_path, monkeypatch):
    # Gemini occasionally swaps a range; the paid analysis stays, the SRT stays playable, and it is flagged.
    data = result()
    data["transcript"][0].update(start_seconds=5, end_seconds=1)
    code, revision = run(tmp_path, monkeypatch, json.dumps(data), "https://youtu.be/dQw4w9WgXcQ")
    assert code == 0
    assert "00:00:05,000 --> 00:00:05,000" in (revision / "transcript.srt").read_text()
    analysis = json.loads((revision / "analysis.json").read_text())["analysis"]
    assert analysis["transcript"][0]["end_seconds"] == 5
    assert any("ended before it started" in n for n in analysis["processing_notes"])


def test_gemini_is_never_offered_processing_notes():
    # The field records what code did; Gemini filling it would blur who wrote each note.
    schema = claims_spec.VideoClaims.model_json_schema()
    assert "processing_notes" not in schema["properties"]
    assert "processing_notes" not in schema.get("required", [])
