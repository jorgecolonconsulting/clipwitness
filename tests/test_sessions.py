import pytest
from claims_spec import SPEC, VideoClaims
from video_core.sessions import SessionStore
from video_core.sources import parse_source


@pytest.fixture
def analysis():
    return VideoClaims.model_validate(
        {
            "summary": "Aquifers",
            "transcript": [
                {"speaker": "Narrator", "start_seconds": 0, "end_seconds": 2, "text": "An aquifer holds water."}
            ],
            "claims": [],
            "visuals": [],
            "uncertainties": [],
        }
    )


def test_persistence_and_versions(tmp_path, analysis):
    store = SessionStore(tmp_path)
    source = parse_source("https://youtu.be/dQw4w9WgXcQ")
    session = store.create("demo", source, "gemini-3.7-flash", "a lamp", "lighting")
    session = SessionStore(tmp_path).load("demo")
    assert (session.brief, session.focus) == ("a lamp", "lighting")
    first = store.export(session, analysis, {"total_cached_tokens": 42}, SPEC)
    second = store.export(session, analysis, {}, SPEC)
    assert first != second
    assert first.is_file() and second.is_file()
    assert "42" in first.read_text()
    assert "unknown" in second.read_text()
    with pytest.raises(FileExistsError):
        store.create("demo", source, "model", None, None)


@pytest.mark.parametrize("name", ["../evil", "/tmp/evil", ".", "a/b", "a\\b", ""])
def test_unsafe_names(tmp_path, name):
    with pytest.raises(ValueError):
        SessionStore(tmp_path).load(name)


def test_exclusive_lock(tmp_path):
    store = SessionStore(tmp_path)
    with (
        store.lock("demo"),
        pytest.raises(RuntimeError, match="busy"),
        store.lock("demo"),
    ):
        pass


def test_export_does_not_overwrite_existing_revision(tmp_path, analysis):
    store = SessionStore(tmp_path)
    session = store.create(
        "demo", parse_source("https://youtu.be/dQw4w9WgXcQ"), "model", None, None
    )
    store.export(session, analysis, {}, SPEC)
    stale = session.model_copy(update={"revision": 0})
    with pytest.raises(FileExistsError):
        store.export(stale, analysis, {}, SPEC)


def test_export_recovers_manifest_write_interruption(tmp_path, analysis, monkeypatch):
    store = SessionStore(tmp_path)
    session = store.create(
        "demo", parse_source("https://youtu.be/dQw4w9WgXcQ"), "model", None, None
    )
    session.pending_interaction_id = "id-completed"
    store.save(session)
    completed = session.model_copy(
        update={"pending_interaction_id": None, "latest_interaction_id": "id-completed"}
    )
    save = store.save

    def interrupted(_session):
        raise OSError("simulated interruption")

    monkeypatch.setattr(store, "save", interrupted)
    with pytest.raises(OSError):
        store.export(completed, analysis, {}, SPEC)
    monkeypatch.setattr(store, "save", save)
    retry = store.load("demo")
    retry.latest_interaction_id = retry.pending_interaction_id
    retry.pending_interaction_id = None
    path = store.export(retry, analysis, {}, SPEC)
    assert path.exists()
    assert store.load("demo").revision == 1
