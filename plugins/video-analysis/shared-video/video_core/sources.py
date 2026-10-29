"""Source validation without downloading public YouTube videos."""

import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .contracts import VideoSource


def parse_source(value: str) -> VideoSource:
    if "://" in value:
        return youtube_source(value)
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Video file does not exist")
    if not path.stat().st_size:
        raise ValueError("Video file is empty")
    mime = mimetypes.guess_type(path.name)[0] or ""
    if not mime.startswith("video/"):
        raise ValueError("Source must have a recognized video extension")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return VideoSource(
        kind="local", identity=digest.hexdigest(), local_path=str(path), mime_type=mime
    )


def youtube_source(value: str) -> VideoSource:
    url = urlparse(value)
    hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    if (
        url.scheme != "https"
        or url.hostname not in hosts
        or url.username
        or url.password
        or url.port
    ):
        raise ValueError("Use an HTTPS YouTube video URL")
    parts = url.path.strip("/").split("/")
    video_id = ""
    if url.hostname == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif url.path == "/watch":
        ids = parse_qs(url.query).get("v", [])
        video_id = ids[0] if len(ids) == 1 else ""
    elif len(parts) == 2 and parts[0] in {"shorts", "embed"}:
        video_id = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("URL must identify one valid YouTube video")
    return VideoSource(
        kind="youtube",
        identity=video_id,
        uri=f"https://www.youtube.com/watch?v={video_id}",
    )
