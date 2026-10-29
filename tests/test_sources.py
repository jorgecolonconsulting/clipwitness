import hashlib

import pytest
from video_core.sources import parse_source


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=abc&t=30",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_identity(url):
    source = parse_source(url)
    assert source.kind == "youtube"
    assert source.identity == "dQw4w9WgXcQ"
    assert source.uri == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    [
        "https://youtube.com.evil.org/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/playlist?list=abc",
        "http://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/invalid",
        "missing.mp4",
    ],
)
def test_invalid_sources(value):
    with pytest.raises(ValueError):
        parse_source(value)


def test_local_identity_and_empty_file(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"video fixture")
    source = parse_source(str(path))
    assert source.identity == hashlib.sha256(b"video fixture").hexdigest()
    assert source.local_path == str(path.resolve())
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        parse_source(str(path))
