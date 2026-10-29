import json

import pytest
from video_core.contracts import InteractionResult, PreparedVideo
from video_core.sessions import SessionStore
from video_core.sources import parse_source
from claims_spec import SPEC
from video_core.agent import VideoAgent


class Provider:
    timeout_seconds = 0.02

    def __init__(self):
        self.requests = []
        self.status = "completed"
        self.text = json.dumps(
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
        assert "visuals" in schema["properties"]
        self.requests.append((video, prompt, instructions, model, previous_id))
        return f"id-{len(self.requests)}"

    def retrieve(self, interaction_id):
        return InteractionResult(
            id=interaction_id,
            status=self.status,
            text=self.text,
            usage={},
            errors=getattr(self, "errors", []),
        )


def create_agent(tmp_path):
    provider = Provider()
    store = SessionStore(tmp_path)
    return VideoAgent(provider, store, SPEC), provider, store


def test_initial_scope_and_refinement(tmp_path):
    agent, provider, store = create_agent(tmp_path)
    first = agent.analyze(
        parse_source("https://youtu.be/dQw4w9WgXcQ"),
        "demo",
        "a lamp",
        "lighting",
        "gemini-3.7-flash",
    )
    assert first.exists()
    assert "lighting" in provider.requests[0][1]
    second = agent.refine("demo", "Reinspect shadows")
    assert second != first and first.exists()
    assert provider.requests[-1][4] == "id-1"
    assert provider.requests[-1][0] is None
    assert "Reinspect" in provider.requests[-1][1]
    assert store.load("demo").focus == "lighting"
    assert store.load("demo").pending_interaction_id is None


def test_resume_pending_without_new_submission(tmp_path):
    agent, provider, store = create_agent(tmp_path)
    provider.status = "in_progress"
    with pytest.raises(TimeoutError):
        agent.analyze(
            parse_source("https://youtu.be/dQw4w9WgXcQ"), "demo", None, None, "model"
        )
    assert store.load("demo").pending_interaction_id == "id-1"
    provider.status = "completed"
    assert agent.resume("demo").exists()
    assert len(provider.requests) == 1  # No duplicate billable request.


def test_invalid_output_preserves_previous_revision(tmp_path):
    agent, provider, store = create_agent(tmp_path)
    first = agent.analyze(
        parse_source("https://youtu.be/dQw4w9WgXcQ"), "demo", None, None, "model"
    )
    provider.text = "{}"
    with pytest.raises(ValueError):
        agent.refine("demo", "Recheck")
    assert first.exists()
    assert store.load("demo").revision == 1
    assert store.load("demo").pending_interaction_id is None
    assert store.load("demo").last_error
    provider.text = json.dumps(
        {
            "summary": "Corrected",
            "transcript": [
                {"speaker": "Narrator", "start_seconds": 0, "end_seconds": 2, "text": "An aquifer holds water."}
            ],
            "claims": [],
            "visuals": [],
            "uncertainties": [],
        }
    )
    assert agent.refine("demo", "Correct the missing structured fields").exists()
    assert provider.requests[-1][4] == "id-1"


def test_failed_interaction_retains_outputs(tmp_path):
    agent, provider, store = create_agent(tmp_path)
    first = agent.analyze(
        parse_source("https://youtu.be/dQw4w9WgXcQ"), "demo", None, None, "model"
    )
    provider.status = "failed"
    provider.errors = ["INTERNAL: There was a problem."]
    with pytest.raises(RuntimeError, match="failed.*INTERNAL: There was a problem"):
        agent.refine("demo", "Recheck")
    assert first.exists()
    assert store.load("demo").pending_interaction_id is None


def test_expired_followup_reanalyzes_with_history(tmp_path):
    from google.genai.errors import ClientError

    agent, provider, store = create_agent(tmp_path)
    agent.analyze(
        parse_source("https://youtu.be/dQw4w9WgXcQ"),
        "demo",
        "a lamp",
        "lighting",
        "model",
    )
    submit = provider.submit

    def expired_once(
        video, prompt, instructions, model, previous_id, schema, thinking_level=None
    ):
        if previous_id:
            raise ClientError(404, {"error": {"message": "expired"}})
        return submit(video, prompt, instructions, model, previous_id, schema)

    provider.submit = expired_once
    assert agent.refine("demo", "Recheck shadows").exists()
    assert provider.requests[-1][0].uri == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "Saved analysis history" in provider.requests[-1][1]
    assert "lighting" in provider.requests[-1][1]
    assert store.load("demo").recovery_note


def test_repeated_expiry_has_one_recovery_attempt(tmp_path):
    from google.genai.errors import ClientError

    agent, provider, store = create_agent(tmp_path)

    def missing(identifier):
        if len(provider.requests) > 2:
            raise AssertionError("Unbounded potentially billable submission")
        raise ClientError(404, {"error": {"message": "expired"}})

    provider.retrieve = missing
    with pytest.raises(RuntimeError, match="recovery failed"):
        agent.analyze(
            parse_source("https://youtu.be/dQw4w9WgXcQ"), "demo", None, None, "model"
        )
    assert len(provider.requests) == 2
    assert store.load("demo").pending_interaction_id is None


def test_resumed_expired_refinement_retains_request(tmp_path):
    from google.genai.errors import ClientError

    agent, provider, store = create_agent(tmp_path)
    agent.analyze(
        parse_source("https://youtu.be/dQw4w9WgXcQ"), "demo", None, "motion", "model"
    )
    provider.status = "in_progress"
    with pytest.raises(TimeoutError):
        agent.refine(
            "demo",
            "Change target to a 12-second ceramic vase and examine camera movement",
        )
    pending = store.load("demo").pending_interaction_id
    retrieve = provider.retrieve

    def expired(identifier):
        if identifier == pending:
            raise ClientError(404, {"error": {"message": "expired"}})
        return retrieve(identifier)

    provider.retrieve = expired
    provider.status = "completed"
    assert agent.resume("demo").exists()
    assert "12-second ceramic vase" in provider.requests[-1][1]
    assert "camera movement" in provider.requests[-1][1]
    assert store.load("demo").pending_prompt is None
