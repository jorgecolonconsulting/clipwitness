import json

import report_html
from test_analyze_video import result, run


def test_report_is_deterministic_and_escapes_model_output(tmp_path, monkeypatch):
    # Gemini's text lands in a page the user opens in a browser: it must never become markup,
    # and the same analysis must always render the same page.
    data = result(on_screen_text="<script>alert(1)</script>")
    data["claims"][0]["quote"] = "<img src=x onerror=alert(1)>"
    data["transcript"][0]["text"] = data["claims"][0]["quote"]
    code, revision = run(tmp_path, monkeypatch, json.dumps(data), "https://youtu.be/dQw4w9WgXcQ")
    assert code == 0
    assert report_html.main([str(revision)]) == 0
    first = (revision / "report.html").read_text()
    report_html.main([str(revision)])
    assert (revision / "report.html").read_text() == first
    assert "<script>" not in first and "<img" not in first  # YouTube: no stills, and nothing injected
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in first
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in first
