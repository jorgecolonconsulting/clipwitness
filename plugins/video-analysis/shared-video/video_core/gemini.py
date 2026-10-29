"""Native Google video interactions and session-owned resource cleanup."""

import base64
import time
from pathlib import Path

import httpx
from google.genai._gaos.lib.compat_errors import APIError as InteractionAPIError
from google.genai.errors import APIError

from .contracts import (
    InteractionResult,
    PreparedVideo,
    Session,
    VideoSource,
)


def field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class GeminiError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(
            f"Gemini interaction request failed (HTTP {code}); check source access, model access and quota"
        )


def interaction_call(operation, **kwargs):
    try:
        return operation(**kwargs)
    except InteractionAPIError as exc:
        raise GeminiError(exc.status_code) from exc


class GeminiVideoClient:
    def __init__(self, client, timeout_seconds: float = 600):
        self.client = client
        self.timeout_seconds = timeout_seconds

    def prepare(self, source: VideoSource, on_upload=None) -> PreparedVideo:
        if source.kind == "youtube":
            # The documented YouTube input has no mime_type.
            return PreparedVideo(uri=source.uri)
        path = Path(source.local_path)
        small = path.stat().st_size <= 14 * 1024 * 1024
        if source.transport == "inline" and not small:
            raise ValueError(
                "Inline transport supports at most 14 MiB; select files transport for larger videos"
            )
        if source.transport == "inline" or (source.transport == "auto" and small):
            return PreparedVideo(
                data=base64.b64encode(path.read_bytes()).decode(),
                mime_type=source.mime_type,
            )
        uploaded = self.client.files.upload(file=source.local_path)
        if on_upload:
            on_upload(uploaded.name)
        deadline = time.monotonic() + self.timeout_seconds
        while field(uploaded.state, "name", uploaded.state) == "PROCESSING":
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Video upload processing timed out; session retains its owned upload"
                )
            time.sleep(min(1, self.timeout_seconds))
            uploaded = self.client.files.get(name=uploaded.name)
        if field(uploaded.state, "name", uploaded.state) != "ACTIVE":
            raise RuntimeError("Google could not process the uploaded video")
        return PreparedVideo(
            uri=uploaded.uri, mime_type=source.mime_type, owned_file=uploaded.name
        )

    def submit(
        self,
        video: PreparedVideo | None,
        prompt: str,
        instructions: str,
        model: str,
        previous_id: str | None,
        schema: dict,
        thinking_level: str | None = None,
    ) -> str:
        inputs = []
        if video:
            content = {"type": "video", "processing": "agentic"}
            if video.mime_type:
                content["mime_type"] = video.mime_type
            if video.data is not None:
                content["data"] = video.data
            else:
                content["uri"] = video.uri
            inputs.append(content)
        inputs.append({"type": "text", "text": prompt})
        request = {
            "model": model,
            "input": inputs,
            "background": True,
            "system_instruction": instructions,
            "response_mime_type": "application/json",
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
        }
        if previous_id:
            request["previous_interaction_id"] = previous_id
        if thinking_level:
            request["generation_config"] = {"thinking_level": thinking_level}
        response = interaction_call(
            self.client.interactions.create, **request, timeout=self.timeout_seconds
        )
        return response.id

    def retrieve(self, interaction_id: str) -> InteractionResult:
        response = interaction_call(
            self.client.interactions.get,
            id=interaction_id,
            timeout=self.timeout_seconds,
        )
        text = "".join(
            field(item, "text", "")
            for item in (field(response, "outputs", []) or [])
            if field(item, "type") == "text"
        )
        usage = field(response, "usage")
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump(exclude_none=True)
        return InteractionResult(
            id=response.id,
            status=response.status,
            text=field(response, "output_text") or text,
            usage=usage or {},
            errors=[
                f"{field(error, 'code')}: {field(error, 'message')}"
                for error in (field(response, "errors", []) or [])
            ],
        )

    def delete_owned(self, session: Session) -> dict[str, str]:
        results = {}
        for name in session.owned_files:
            results[name] = self.delete_resource(self.client.files, "name", name)
        for interaction_id in session.owned_interactions:
            results[interaction_id] = self.delete_resource(
                self.client.interactions, "id", interaction_id
            )
        return results

    def delete_resource(self, api, parameter: str, identifier: str) -> str:
        try:
            api.delete(**{parameter: identifier})
            return "deleted"
        except (APIError, InteractionAPIError, httpx.HTTPError, RuntimeError) as exc:
            # Already gone (Google expires uploads after 48 h): nothing left to delete or retry.
            if 404 in (getattr(exc, "code", None), getattr(exc, "status_code", None)):
                return "deleted"
            # Cleanup is best-effort; retain failed ownership for retry.
            return "failed"
