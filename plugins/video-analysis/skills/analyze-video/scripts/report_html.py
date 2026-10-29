#!/usr/bin/env python3
"""Render a published revision (analysis.json + frames/) as report.html beside it, optionally serve it.

Deterministic: the same analysis.json always gives the same page. Standard library only, so any python3
(macOS ships 3.9) runs it without the plugin's environment.

Usage: python3 report_html.py <revision-dir> [--fps 30] [--serve [PORT]]
"""

import argparse
import html
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CSS = """
body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#1d1d1f;background:#fbfaf7}
h1{margin:0 0 4px} .lede{color:#555;max-width:75ch} .stats{display:flex;gap:28px;margin:18px 0 28px;flex-wrap:wrap}
.stat b{display:block;font-size:24px} .stat{color:#666;font-size:13px} h2{margin-top:40px;border-bottom:1px solid #ddd;padding-bottom:6px}
section{margin:22px 0 30px} h3{margin:0;font-size:15px} .cid{background:#1d1d1f;color:#fff;border-radius:4px;padding:1px 7px;font-size:12px}
.t{color:#777;font-weight:400;font-variant-numeric:tabular-nums} blockquote{margin:8px 0 12px;font-size:18px;border-left:3px solid #c8553d;padding-left:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{display:flex;gap:12px;background:#fff;border:1px solid #e6e3dc;border-radius:8px;padding:10px}
.thumb{position:relative;width:96px;flex:none;align-self:flex-start} .thumb img{width:96px;display:block;border-radius:4px}
.box{position:absolute;border:2px solid #ff2d55;border-radius:2px;box-shadow:0 0 0 999px rgba(0,0,0,.25)} .thumb{overflow:hidden;border-radius:4px}
.tc{font:600 18px ui-monospace,Menlo,monospace} .sub{font-size:12px;color:#777} .kind{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#999;margin:4px 0 2px}
.src{padding:0 5px;border-radius:3px} .ffmpeg{background:#dff3e4;color:#1e6b34} .gemini{background:#fdf0d5;color:#8a5a00}
.ost{margin-top:4px;font-style:italic;color:#444;white-space:pre-line} table{border-collapse:collapse;width:100%} td{padding:6px 10px;border-bottom:1px solid #eee;vertical-align:top}
.none{color:#8a5a00;font-size:13px;margin:4px 0}
"""


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def card(visual: dict, fps: float) -> str:
    # box_2d is [ymin, xmin, ymax, xmax] on a 0-1000 grid; CSS wants percentages.
    ymin, xmin, ymax, xmax = visual["box_2d"]
    box = (
        f"top:{ymin / 10:.1f}%;left:{xmin / 10:.1f}%;"
        f"height:{(ymax - ymin) / 10:.1f}%;width:{(xmax - xmin) / 10:.1f}%"
    )
    onset = visual["onset_seconds"]
    source = visual.get("timing_source") or ""
    gemini = visual.get("gemini_onset_seconds")
    if gemini is not None:
        delta = int(round((onset - gemini) * fps))
        gemini_text = f" · Gemini {gemini:.2f}s ({delta:+d}f)"
    else:
        gemini_text = ""
    ost = visual.get("on_screen_text")
    ost_html = f'<div class=ost>“{esc(ost)}”</div>' if ost else ""
    # YouTube sources and failed snaps have no still; keep the card, drop the thumbnail.
    thumb = (
        f'<div class=thumb><img src="{esc(visual["frame_path"])}">'
        f'<div class=box style="{box}"></div></div>'
        if visual.get("frame_path")
        else ""
    )
    return (
        f"<div class=card>{thumb}"
        f'<div class=meta><div class=tc>{esc(visual.get("onset_tc"))}</div>'
        f'<div class=sub>{onset:.3f}s · <span class="src {esc(source)}">{esc(source)}</span>{gemini_text}</div>'
        f'<div class=kind>{esc(visual["kind"])} · {esc(visual["id"])}</div>'
        f'<div>{esc(visual["description"])}</div>{ost_html}</div></div>'
    )


def render(rev: Path, fps: float) -> str:
    data = json.loads((rev / "analysis.json").read_text())
    meta, a = data["metadata"], data["analysis"]
    usage = meta.get("usage") or {}
    visuals, claims, transcript = a["visuals"], a["claims"], a["transcript"]
    video_tokens = sum(
        t.get("tokens", 0)
        for t in usage.get("tool_use_tokens_by_modality") or []
        if t.get("modality") == "video"
    )
    snapped = sum(1 for v in visuals if v.get("timing_source") == "ffmpeg")
    level = (meta.get("options") or {}).get("thinking_level") or "model default"
    orphan = [c["id"] for c in claims if not any(c["id"] in v["claim_ids"] for v in visuals)]
    source = meta["source"]
    name = Path(source["local_path"]).name if source.get("local_path") else source.get("uri") or ""
    title = Path(source["local_path"]).stem if source.get("local_path") else source.get("identity") or ""
    total = usage.get("total_tokens")

    parts = [
        "<!doctype html><meta charset=utf-8>",
        f"<title>analyze-video · {esc(title)}</title>",
        f"<style>{CSS}</style>",
        "<h1>Video claims analysis</h1>",
        f'<div class=t>{esc(name)} · {esc(meta["model"])} · '
        f'thinking {esc(level)} · {esc(source.get("transport") or source["kind"])} · {esc(rev.name)}</div>',
        f'<p class=lede>{esc(a["summary"])}</p>',
        "<div class=stats>"
        f"<div class=stat><b>{len(transcript)}</b>transcript segments</div>"
        f"<div class=stat><b>{len(claims)}</b>claims</div>"
        f"<div class=stat><b>{len(visuals)}</b>visuals</div>"
        f"<div class=stat><b>{snapped}/{len(visuals)}</b>frame-snapped</div>"
        f"<div class=stat><b>{len(orphan)}</b>claims without a visual</div>"
        f"<div class=stat><b>{video_tokens:,}</b>video tokens</div>"
        f"<div class=stat><b>{f'{total:,}' if total is not None else 'unknown'}</b>total tokens</div>"
        "</div>",
        "<h2>Claims and the visuals tied to them</h2>",
    ]

    for c in claims:
        tied = [v for v in visuals if c["id"] in v["claim_ids"]]
        parts.append(
            f'<section><h3><span class=cid>{esc(c["id"])}</span> '
            f'<span class=t>{c["start_seconds"]:.1f}s · {esc(c.get("speaker") or "unknown")} · {esc(c["source"])}</span></h3>'
            f'<blockquote>{esc(c["quote"])}</blockquote>'
        )
        if tied:
            parts.append("<div class=grid>" + "".join(card(v, fps) for v in tied) + "</div>")
        else:
            parts.append("<div class=none>No visual tied to this claim.</div>")
        parts.append("</section>")

    parts.append("<h2>Transcript</h2><table>")
    for s in transcript:
        parts.append(
            f'<tr><td class=t>{s["start_seconds"]:.2f}–{s["end_seconds"]:.2f}s</td>'
            f'<td><b>{esc(s["speaker"])}</b></td><td>{esc(s["text"])}</td></tr>'
        )
    parts.append("</table>")

    parts.append("<h2>Uncertainties</h2><ul>")
    if a.get("uncertainties"):
        parts.extend(f"<li>{esc(u)}</li>" for u in a["uncertainties"])
    else:
        parts.append("<li>None reported</li>")
    parts.append("</ul>")
    if a.get("processing_notes"):
        parts.append("<h2>Processing notes</h2><ul>")
        parts.extend(f"<li>{esc(n)}</li>" for n in a["processing_notes"])
        parts.append("</ul>")
    return "\n".join(parts) + "\n"


def serve(rev: Path, port: int) -> None:
    # Loopback only: the report holds the video's stills and transcript.
    handler = partial(SimpleHTTPRequestHandler, directory=str(rev))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        # Codex's default sandbox forbids opening a port; a busy PORT fails the same way.
        sys.exit(
            f"Cannot serve on 127.0.0.1:{port} ({exc.strerror}). In Codex, approve running this command "
            "outside the sandbox; otherwise pick another port or omit it."
        )
    print(f"http://127.0.0.1:{server.server_address[1]}/report.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("revision_dir", type=Path, help="Folder holding analysis.json (beside analysis.md)")
    parser.add_argument("--fps", type=float, help="Frame rate for Gemini-vs-ffmpeg deltas (default: the run's --fps, else 30)")
    parser.add_argument("--serve", type=int, nargs="?", const=0, metavar="PORT",
                        help="Serve the report on 127.0.0.1 until stopped (no PORT: any free port)")
    args = parser.parse_args(argv)
    rev = args.revision_dir.expanduser().resolve()
    if not (rev / "analysis.json").is_file():
        sys.exit(f"No analysis.json in {rev}")
    meta = json.loads((rev / "analysis.json").read_text())["metadata"]
    fps = args.fps or (meta.get("options") or {}).get("fps") or 30.0
    out = rev / "report.html"
    out.write_text(render(rev, fps))
    print(out, flush=True)
    if args.serve is not None:
        serve(rev, args.serve)
    return 0


if __name__ == "__main__":
    sys.exit(main())
