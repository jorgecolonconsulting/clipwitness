"""Validated source, provider and durable session contracts shared by every video skill."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoSource(Contract):
    kind: Literal["local", "youtube"]
    identity: str
    local_path: str | None = None
    uri: str | None = None
    mime_type: str = "video/mp4"
    transport: Literal["auto", "files", "inline"] = "auto"


class Session(Contract):
    name: str
    source: VideoSource
    model: str
    brief: str | None = None
    focus: str | None = None
    options: dict = Field(default_factory=dict)
    latest_interaction_id: str | None = None
    pending_interaction_id: str | None = None
    pending_prompt: str | None = None
    last_error: str | None = None
    owned_files: list[str] = Field(default_factory=list)
    owned_interactions: list[str] = Field(default_factory=list)
    revision: int = 0
    recovery_note: str | None = None


class PreparedVideo(Contract):
    uri: str | None = None
    data: str | None = None
    mime_type: str | None = None
    owned_file: str | None = None


class InteractionResult(Contract):
    id: str
    status: str
    text: str = ""
    usage: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
