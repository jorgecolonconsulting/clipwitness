import json

import pytest
import analyze_video
from video_core import cli
from video_core.sessions import SessionStore
from video_core.sources import parse_source


def test_help(capsys):
    with pytest.raises(SystemExit) as result:
        analyze_video.main(["--help"])
    assert result.value.code == 0
    assert "analyze" in capsys.readouterr().out


def test_status_without_credential(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    SessionStore(tmp_path).create(
        "demo",
        parse_source("https://youtu.be/dQw4w9WgXcQ"),
        "model",
        "lamp",
        "lighting",
    )
    assert (
        analyze_video.main(
            ["--session-root", str(tmp_path), "status", "--session", "demo"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["focus"] == "lighting"


def test_missing_credential_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert (
        analyze_video.main(
            [
                "--session-root",
                str(tmp_path),
                "analyze",
                "https://youtu.be/dQw4w9WgXcQ",
                "--session",
                "demo",
                "--focus",
                "motion",
            ]
        )
        == 1
    )
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_analyze_dispatch_and_refine(tmp_path, monkeypatch, capsys):
    from test_workflow import Provider

    monkeypatch.setattr(cli, "create_provider", lambda timeout: Provider())
    assert (
        analyze_video.main(
            [
                "--session-root",
                str(tmp_path),
                "analyze",
                "https://youtu.be/dQw4w9WgXcQ",
                "--session",
                "demo",
                "--focus",
                "lighting",
            ]
        )
        == 0
    )
    path = capsys.readouterr().out.strip()
    assert "revision-0001" in path
    assert (
        analyze_video.main(
            [
                "--session-root",
                str(tmp_path),
                "refine",
                "--session",
                "demo",
                "--prompt",
                "Reinspect",
            ]
        )
        == 0
    )
    assert "revision-0002" in capsys.readouterr().out


def test_cleanup_preserves_source_and_failed_ownership(tmp_path, monkeypatch, capsys):
    from test_gemini import Service
    from video_core.gemini import GeminiVideoClient

    source_path = tmp_path / "video.mp4"
    source_path.write_bytes(b"video")
    store = SessionStore(tmp_path / "sessions")
    session = store.create("demo", parse_source(str(source_path)), "model", None, None)
    session.owned_files = ["files/owned", "files/fail"]
    session.owned_interactions = ["id-1"]
    session.latest_interaction_id = "id-1"
    session.pending_interaction_id = "id-1"
    store.save(session)
    service = Service()
    monkeypatch.setattr(
        cli,
        "create_provider",
        lambda timeout: GeminiVideoClient(service, timeout),
    )
    assert (
        analyze_video.main(
            ["--session-root", str(store.root), "cleanup", "--session", "demo"]
        )
        == 1
    )
    session = store.load("demo")
    assert session.owned_files == ["files/fail"]
    assert session.latest_interaction_id is None
    assert session.pending_interaction_id is None
    assert source_path.exists()
    assert "failed" in capsys.readouterr().err


def test_error_redacts_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "private-test-key")

    def fail(timeout):
        raise ValueError("Invalid private-test-key")

    monkeypatch.setattr(cli, "create_provider", fail)
    assert (
        analyze_video.main(
            ["--session-root", str(tmp_path), "cleanup", "--session", "demo"]
        )
        == 1
    )
    assert "private-test-key" not in capsys.readouterr().err


def test_cli_resume(tmp_path, monkeypatch, capsys):
    from test_workflow import Provider

    provider = Provider()
    provider.status = "in_progress"
    monkeypatch.setattr(cli, "create_provider", lambda timeout: provider)
    base = ["--session-root", str(tmp_path)]
    assert (
        analyze_video.main(
            base + ["analyze", "https://youtu.be/dQw4w9WgXcQ", "--session", "demo"]
        )
        == 1
    )
    assert "resume" in capsys.readouterr().err
    provider.status = "completed"
    assert analyze_video.main(base + ["resume", "--session", "demo"]) == 0
    assert "revision-0001" in capsys.readouterr().out


def test_nonpositive_timeout(tmp_path, capsys):
    assert (
        analyze_video.main(
            [
                "--session-root",
                str(tmp_path),
                "--timeout",
                "0",
                "status",
                "--session",
                "demo",
            ]
        )
        == 1
    )
    assert "positive" in capsys.readouterr().err


def test_small_local_video_uploads_by_default(tmp_path, monkeypatch):
    # Uploads expire after 48 h; inline bytes would sit in the stored interaction for 55 days.
    class Provider:
        def prepare(self, source, on_upload=None):
            transports.append(source.transport)
            raise RuntimeError("stop")

    transports = []
    monkeypatch.setattr(cli, "create_provider", lambda timeout: Provider())
    video = tmp_path / "small.mp4"
    video.write_bytes(b"video bytes")
    argv = ["--session-root", str(tmp_path), "analyze", str(video), "--session", "s"]
    assert analyze_video.main(argv) == 1
    assert transports == ["files"]
