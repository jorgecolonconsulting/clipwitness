from types import SimpleNamespace as NS

import httpx
import pytest
from google.genai._gaos.lib.compat_errors import APIError as InteractionAPIError
from google.genai.errors import ClientError
from video_core.contracts import Session
from video_core.gemini import GeminiVideoClient
from video_core.sources import parse_source


class Service:
    def __init__(self):
        self.files = self
        self.interactions = self
        self.requests = []
        self.state = "ACTIVE"
        self.status = "completed"
        self.usage = {"total_cached_tokens": 123}
        self.uploaded = False
        self.deleted = []

    def upload(self, **kwargs):
        self.uploaded = True
        return NS(
            name="files/owned", uri="https://files/owned", state=NS(name=self.state)
        )

    def get(self, name=None, id=None, **kwargs):
        if name:
            return NS(name=name, uri="https://files/owned", state=NS(name=self.state))
        return NS(
            id=id,
            status=self.status,
            outputs=[NS(type="text", text="{}")],
            usage=self.usage,
        )

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return NS(id="interaction-1")

    def delete(self, name=None, id=None, **kwargs):
        if name == "files/fail":
            raise RuntimeError("provider failure")
        if name == "files/expired":
            raise ClientError(404, {"error": {"message": "not found"}})
        if id == "interaction-expired":
            error = InteractionAPIError("not found", httpx.Request("DELETE", "https://x"), body=None)
            error.status_code = 404
            raise error
        self.deleted.append(name or id)


def test_youtube_native_request_and_followup():
    service = Service()
    provider = GeminiVideoClient(service, 1)
    video = provider.prepare(parse_source("https://youtu.be/dQw4w9WgXcQ"))
    assert not service.uploaded
    assert (
        provider.submit(video, "inspect", "stable", "gemini-3.7-flash", None, {})
        == "interaction-1"
    )
    request = service.requests[-1]
    assert request["background"] is True
    assert request["input"][0]["processing"] == "agentic"
    assert request["input"][0]["uri"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "mime_type" not in request["input"][0]
    provider.submit(
        None, "reinspect", "stable", "gemini-3.7-flash", "interaction-1", {}
    )
    assert service.requests[-1]["previous_interaction_id"] == "interaction-1"
    assert service.requests[-1]["system_instruction"] == "stable"
    assert provider.retrieve("interaction-1").usage["total_cached_tokens"] == 123
    service.usage = None
    assert provider.retrieve("interaction-1").usage == {}


@pytest.mark.parametrize(
    "state,error", [("FAILED", RuntimeError), ("PROCESSING", TimeoutError)]
)
def test_upload_errors(tmp_path, state, error):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    service = Service()
    service.state = state
    with pytest.raises(error):
        GeminiVideoClient(service, 0.01).prepare(
            parse_source(str(path)).model_copy(update={"transport": "files"})
        )


def test_upload_active_and_cleanup(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    service = Service()
    provider = GeminiVideoClient(service, 1)
    source = parse_source(str(path)).model_copy(update={"transport": "files"})
    assert provider.prepare(source).owned_file == "files/owned"
    session = Session(
        name="demo",
        source=source,
        model="model",
        owned_files=["files/owned", "files/fail", "files/expired"],
        owned_interactions=["interaction-1", "interaction-expired"],
    )
    results = provider.delete_owned(session)
    assert results["files/owned"] == "deleted"
    assert results["files/fail"] == "failed"
    # Expired on Google's side: retrying can never succeed, so cleanup must not keep reporting failure.
    assert results["files/expired"] == "deleted"
    assert results["interaction-expired"] == "deleted"
    assert "files/other" not in service.deleted
    assert path.exists()


def test_current_sdk_output_text():
    service = Service()
    service.get = lambda **kwargs: NS(
        id=kwargs["id"],
        status="completed",
        output_text='{"summary":"observed"}',
        usage={},
    )
    assert (
        GeminiVideoClient(service, 1).retrieve("id-1").text == '{"summary":"observed"}'
    )


def test_failed_interaction_keeps_provider_errors():
    service = Service()
    service.get = lambda **kwargs: NS(
        id=kwargs["id"],
        status="failed",
        output_text="",
        errors=[{"code": "INTERNAL", "message": "There was a problem."}],
    )
    result = GeminiVideoClient(service, 1).retrieve("id-1")
    assert result.errors == ["INTERNAL: There was a problem."]


def test_interactions_error_has_safe_status():
    import httpx
    from google.genai._gaos.lib.compat_errors import BadRequestError
    from video_core.gemini import GeminiError

    service = Service()

    def fail(**kwargs):
        raise BadRequestError(
            "secret request details",
            response=httpx.Response(
                400, request=httpx.Request("GET", "https://example.test")
            ),
            body={},
        )

    service.get = fail
    with pytest.raises(GeminiError) as failure:
        GeminiVideoClient(service, 1).retrieve("id-1")
    assert failure.value.code == 400
    assert "secret" not in str(failure.value)


def test_small_local_video_inline_agentic(tmp_path):
    import base64

    service = Service()
    provider = GeminiVideoClient(service, 1)
    path = tmp_path / "small.mp4"
    path.write_bytes(b"video bytes")
    video = provider.prepare(parse_source(str(path)))
    provider.submit(video, "inspect", "stable", "gemini-3.7-flash", None, {})
    content = service.requests[-1]["input"][0]
    assert content["processing"] == "agentic"
    assert content["data"] == base64.b64encode(b"video bytes").decode()
    assert "uri" not in content
    assert not service.uploaded


def test_inline_limit_rejected_before_upload(tmp_path):
    path = tmp_path / "large.mp4"
    with path.open("wb") as stream:
        stream.truncate(14 * 1024 * 1024 + 1)
    source = parse_source(str(path)).model_copy(update={"transport": "inline"})
    service = Service()
    with pytest.raises(ValueError, match="14 MiB"):
        GeminiVideoClient(service, 1).prepare(source)
    assert not service.uploaded


def test_upload_ownership_recorded_on_processing_failure(tmp_path):
    path = tmp_path / "failed.mp4"
    path.write_bytes(b"video")
    service = Service()
    service.state = "FAILED"
    names = []
    source = parse_source(str(path)).model_copy(update={"transport": "files"})
    with pytest.raises(RuntimeError):
        GeminiVideoClient(service, 1).prepare(source, on_upload=names.append)
    assert names == ["files/owned"]


def test_thinking_level_sent_only_when_chosen():
    service = Service()
    provider = GeminiVideoClient(service, 1)
    provider.submit(None, "inspect", "stable", "model", None, {})
    assert "generation_config" not in service.requests[-1]
    provider.submit(None, "inspect", "stable", "model", None, {}, "high")
    assert service.requests[-1]["generation_config"] == {"thinking_level": "high"}
